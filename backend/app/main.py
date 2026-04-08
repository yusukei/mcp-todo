import logging
import os
import sys
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from .core.config import settings
from .core.database import close_db, connect, get_mongo_client
from .core.redis import close_redis, get_redis, init_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

def _is_weak_secret(value: str, placeholder: str) -> bool:
    """Reject empty, placeholder, or sub-32-byte HMAC keys.

    PyJWT does not refuse to encode/decode with an empty key, so an unset
    SECRET_KEY silently produces forgeable tokens (any attacker can sign
    JWTs with the same empty key and impersonate any user). Reject every
    weak form at startup so the operator must supply a real value.
    """
    return (not value) or value == placeholder or len(value) < 32


if _is_weak_secret(settings.SECRET_KEY, "change-me"):
    print(
        "FATAL: SECRET_KEY is missing, placeholder, or under 32 bytes. "
        "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'",
        file=sys.stderr,
    )
    sys.exit(1)

if _is_weak_secret(settings.REFRESH_SECRET_KEY, "change-me-refresh"):
    print(
        "FATAL: REFRESH_SECRET_KEY is missing, placeholder, or under 32 bytes. "
        "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'",
        file=sys.stderr,
    )
    sys.exit(1)

if not settings.FRONTEND_URL.strip():
    print(
        "FATAL: FRONTEND_URL is empty. The agent WebSocket endpoint refuses "
        "connections without an explicit Origin allowlist (CSWSH defense) and "
        "the allowlist is derived from FRONTEND_URL. Set FRONTEND_URL in your "
        "environment, e.g.: FRONTEND_URL=https://todo.example.com",
        file=sys.stderr,
    )
    sys.exit(1)


class ORJSONResponse(JSONResponse):
    def render(self, content: object) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_NAIVE_UTC)



def _should_rebuild(idx) -> bool:
    """Decide whether a search index needs a full rebuild on startup.

    Rebuild when:
    - FORCE_REINDEX env var is truthy (operator override), OR
    - the index was cleared due to a schema change, OR
    - the index has no documents on disk.

    Skipping rebuild lets large deployments restart in seconds; the
    on-write index_task / deindex_task hooks keep the index in sync
    while the process runs.
    """
    if os.environ.get("FORCE_REINDEX", "").lower() in ("1", "true", "yes"):
        return True
    if getattr(idx, "schema_cleared", False):
        return True
    return idx.is_empty()


# Search index registry: each entry shares the same Tantivy init/rebuild lifecycle.
# (label, module_path, index_cls, indexer_cls, service_cls, index_dir_setting, unit_label)
_SEARCH_INDEX_REGISTRY: list[tuple[str, str, str, str, str, str, str]] = [
    ("Search", "app.services.search",
     "SearchIndex", "SearchIndexer", "SearchService",
     "SEARCH_INDEX_DIR", "tasks"),
    ("Knowledge search", "app.services.knowledge_search",
     "KnowledgeSearchIndex", "KnowledgeSearchIndexer", "KnowledgeSearchService",
     "KNOWLEDGE_INDEX_DIR", "entries"),
    ("Document search", "app.services.document_search",
     "DocumentSearchIndex", "DocumentSearchIndexer", "DocumentSearchService",
     "DOCUMENT_INDEX_DIR", "entries"),
    ("DocSite search", "app.services.docsite_search",
     "DocSiteSearchIndex", "DocSiteSearchIndexer", "DocSiteSearchService",
     "DOCSITE_INDEX_DIR", "pages"),
    ("Bookmark search", "app.services.bookmark_search",
     "BookmarkSearchIndex", "BookmarkSearchIndexer", "BookmarkSearchService",
     "BOOKMARK_INDEX_DIR", "bookmarks"),
]


async def _init_search_index(
    label: str,
    module_path: str,
    index_cls_name: str,
    indexer_cls_name: str,
    service_cls_name: str,
    index_dir_setting: str,
    unit_label: str,
):
    """Returns the indexer instance (or None if disabled/failed) so the
    caller can attach a flush_loop background task."""
    import importlib
    from pathlib import Path

    try:
        module = importlib.import_module(module_path)
    except Exception as e:
        logger.warning("Failed to import %s module: %s", label, e)
        return None

    if not getattr(module, "TANTIVY_AVAILABLE", False):
        if label == "Search":
            logger.info("tantivy not available — full-text search disabled (using $regex fallback)")
        return None

    try:
        index_cls = getattr(module, index_cls_name)
        indexer_cls = getattr(module, indexer_cls_name)
        service_cls = getattr(module, service_cls_name)

        index = index_cls(Path(getattr(settings, index_dir_setting)))
        indexer = indexer_cls(index)
        indexer_cls.set_instance(indexer)
        service_cls.set_instance(service_cls(index))

        if _should_rebuild(index):
            count = await indexer.rebuild()
            logger.info("%s index rebuilt: %d %s indexed", label, count, unit_label)
        else:
            logger.info(
                "%s index loaded from disk: %d documents (skip rebuild)",
                label, index.doc_count(),
            )
        return indexer
    except Exception as e:
        logger.warning("Failed to initialize %s index: %s", label, e)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    init_redis()

    # Warn about default DB passwords
    if "changeme" in settings.MONGO_URI.lower():
        logger.warning("MONGO_URI contains default password 'changeme' — change it for production")
    if "changeme" in settings.REDIS_URI.lower() or "changeme" in settings.REDIS_MCP_URI.lower():
        logger.warning("REDIS_URI contains default password 'changeme' — change it for production")

    # Auto-create admin user from env vars if set
    if settings.INIT_ADMIN_EMAIL and settings.INIT_ADMIN_PASSWORD:
        from .cli import create_admin_user
        try:
            await create_admin_user(
                settings.INIT_ADMIN_EMAIL,
                settings.INIT_ADMIN_PASSWORD,
                "Admin",
            )
        except Exception as e:
            logger.warning("Failed to auto-create admin user: %s", e)

    # ── Startup cleanup: reset stale state from previous process ──
    #
    # The previous ``reset_all_agents_online()`` call was removed when
    # the ``is_online`` DB field was retired: "is this agent connected
    # right now" is derived from ``agent_manager.is_connected()`` which
    # is authoritative per-process. A persisted flag could not safely
    # be cleared across multiple workers/replicas without a race, so
    # there is nothing to reset at startup.
    _is_testing = os.environ.get("TESTING") == "1"
    if not _is_testing:
        from .services.chat_events import recover_stale_sessions
        recovered_sessions = await recover_stale_sessions()
        if recovered_sessions:
            logger.info("Recovered %d stale busy chat sessions", recovered_sessions)

    # ── Search index initialization (registry-driven) ─────────
    _flush_tasks: list = []
    _flush_indexers: list = []
    if not _is_testing:
        import asyncio as _asyncio
        for entry in _SEARCH_INDEX_REGISTRY:
            indexer = await _init_search_index(*entry)
            if indexer is not None and hasattr(indexer, "flush_loop"):
                _flush_indexers.append(indexer)
                _flush_tasks.append(_asyncio.create_task(indexer.flush_loop()))

    # ── Clip queue worker ─────────────────────────────────────
    if not _is_testing:
        from .services.clip_queue import clip_queue
        await clip_queue.start()
        recovered = await clip_queue.recover_pending()
        if recovered:
            logger.info("Clip queue: recovered %d pending bookmarks", recovered)

    # ── MCP server integration ────────────────────────────────
    from .mcp.server import MCP_PATH, MOUNT_PREFIX, register_tools
    from .mcp.server import mcp as _mcp_server

    register_tools()

    # Use a local copy of FastMCP's create_streamable_http_app() that
    # instantiates ResilientSessionManager directly. Eliminates the
    # previous monkey-patch on `_fmcp_http.StreamableHTTPSessionManager`.
    from .mcp.streamable_http_app import create_resilient_streamable_http_app
    from .mcp.session_store import RedisEventStore
    event_store = RedisEventStore()
    _mcp_app = create_resilient_streamable_http_app(
        server=_mcp_server,
        streamable_http_path=MCP_PATH,
        event_store=event_store,
        auth=_mcp_server.auth,
    )

    # Register well-known routes at root level (before MCP mount)
    # FastMCP の OAuthProvider が自動生成するルートを使用（手動ルートは廃止）
    from .mcp.server import _oauth_provider
    for route in _oauth_provider.get_well_known_routes(mcp_path=MCP_PATH):
        app.routes.insert(0, route)

    # OAuth consent screen router
    from .mcp.oauth_consent import router as mcp_consent_router
    app.include_router(mcp_consent_router)

    # Mount MCP subapp
    app.mount(MOUNT_PREFIX, _mcp_app)
    logger.info("MCP server mounted at %s (stateful + RedisEventStore + OAuth)", MOUNT_PREFIX)

    # MCP subapp lifespan (Starlette mount doesn't auto-execute subapp lifespan)
    #
    # Shutdown ordering note: ``agent_manager.start_shutdown()`` MUST run
    # while the MCP subapp is still alive, otherwise MCP tool handlers
    # mid-await on ``send_request`` get cancelled by the FastMCP teardown
    # before they have a chance to either complete or observe the
    # shutdown flag. We do that in the ``finally`` of an inner try below.
    # ``drain()`` then runs *after* the MCP subapp has unwound, by which
    # point any in-flight handler is either done or has had its future
    # rejected by the agent disconnect path — both reach the drain event.
    from .services.agent_manager import agent_manager as _agent_mgr
    if not _is_testing:
        async with _mcp_app.lifespan(_mcp_app):
            try:
                yield
            finally:
                # Stop accepting new agent requests while MCP tool
                # handlers can still observe the rejection. New
                # callers raise AgentShuttingDownError immediately;
                # in-flight callers continue to completion.
                _agent_mgr.start_shutdown()
    else:
        try:
            yield
        finally:
            _agent_mgr.start_shutdown()

    # Shutdown
    # Wait for in-flight RPCs to finish. Long-running remote_exec
    # calls can take up to REMOTE_MAX_TIMEOUT_SECONDS, so this drain
    # is the only thing standing between a clean restart and a wave
    # of user-visible CommandTimeoutError responses.
    await _agent_mgr.drain(timeout=settings.AGENT_SHUTDOWN_DRAIN_TIMEOUT_SECONDS)
    if not _is_testing:
        from .services.clip_queue import clip_queue
        await clip_queue.stop()
    # Cancel flush_loop tasks and drain pending writes.
    for _t in _flush_tasks:
        _t.cancel()
    for _t in _flush_tasks:
        try:
            await _t
        except (Exception, BaseException):
            pass
    for _idx in _flush_indexers:
        try:
            import asyncio as _asyncio2
            await _asyncio2.to_thread(_idx.flush)
        except Exception as _e:
            logger.warning("Final flush failed: %s", _e)
    await event_store.aclose()
    from .mcp.oauth import close_mcp_redis
    await close_mcp_redis()
    await close_redis()
    await close_db()


app = FastAPI(
    title="MCP Todo API",
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

# Trailing slash middleware for MCP
from .mcp.well_known import McpTrailingSlashMiddleware  # noqa: E402
app.add_middleware(McpTrailingSlashMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return ORJSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# Routers
from .api.v1.endpoints import attachments, auth, backup, bookmark_assets, bookmarks, chat, docsites, documents, events, knowledge, mcp_keys, mcp_usage, projects, tasks, users, workspaces  # noqa: E402

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(mcp_keys.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(attachments.router, prefix="/api/v1")
app.include_router(backup.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(docsites.router, prefix="/api/v1")
app.include_router(bookmarks.coll_router, prefix="/api/v1")
app.include_router(bookmarks.bm_router, prefix="/api/v1")
app.include_router(bookmark_assets.router, prefix="/api/v1")
app.include_router(workspaces.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(mcp_usage.router, prefix="/api/v1")


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint.

    Exposes the default ``prometheus_client`` registry. Authentication
    is intentionally not enforced here — the endpoint is blocked from
    external traffic by the ``location = /metrics`` rule in
    ``nginx/nginx.conf`` (which returns 404 to all callers by
    default). Operators who want to scrape this with a real
    Prometheus server must replace that nginx block with an explicit
    ``allow`` list per the comment there.

    The endpoint exposes operational shape (number of agents, op
    latencies, per-agent labels) which is not sensitive in itself
    but should not be public.
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health() -> JSONResponse:
    checks: dict = {"status": "ok"}
    # MongoDB ping
    try:
        client = get_mongo_client()
        if client:
            await client.admin.command("ping")
        checks["mongo"] = "ok"
    except Exception:
        checks["mongo"] = "down"
        checks["status"] = "unhealthy"
    # Redis ping
    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"
        checks["status"] = "unhealthy"

    status_code = 503 if checks["status"] == "unhealthy" else 200
    return JSONResponse(checks, status_code=status_code)
