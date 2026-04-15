import asyncio
import contextlib
import logging
import os
import sys
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

    # Wire the agent connection manager to Redis so cross-worker
    # routing can take effect immediately. start() is idempotent and
    # the bus listeners are bound to this event loop, so the
    # subsequent shutdown.stop() unwinds them cleanly.
    from .services.agent_manager import agent_manager as _agent_mgr_startup
    await _agent_mgr_startup.start()

    # Same pattern for the chat connection manager: its background
    # subscriber turns Redis pub/sub messages into local WebSocket
    # fan-out so a chat_event published by worker A reaches every
    # browser regardless of which worker holds the WebSocket.
    from .services.chat_manager import chat_manager as _chat_mgr_startup
    await _chat_mgr_startup.start()

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
    # ``recover_stale_sessions`` is API-side state recovery (chat
    # sessions that were busy when the API worker crashed), so it
    # runs with ENABLE_API. The indexer sidecar does not need it.
    if not _is_testing and settings.ENABLE_API:
        from .services.chat_events import recover_stale_sessions
        recovered_sessions = await recover_stale_sessions()
        if recovered_sessions:
            logger.info("Recovered %d stale busy chat sessions", recovered_sessions)

    # ── Search index initialization (registry-driven) ─────────
    #
    # Tantivy ``IndexWriter`` is a single-writer resource: only one
    # process can hold the write lock on a given index directory at
    # a time. When the multi-worker sidecar is rolled out (see
    # ``docs/architecture/multi-worker-sidecar.md``) the API
    # containers run with ``ENABLE_INDEXERS=0`` and the dedicated
    # ``backend-indexer`` sidecar owns the writers exclusively.
    _flush_tasks: list = []
    _flush_indexers: list = []
    if not _is_testing and settings.ENABLE_INDEXERS:
        import asyncio as _asyncio
        for entry in _SEARCH_INDEX_REGISTRY:
            indexer = await _init_search_index(*entry)
            if indexer is not None and hasattr(indexer, "flush_loop"):
                _flush_indexers.append(indexer)
                _flush_tasks.append(_asyncio.create_task(indexer.flush_loop()))

        # Start the Redis Stream consumer so this indexer picks up
        # notifications published by API workers (multi-worker
        # sidecar topology). In single-process deployments this is
        # still safe because ENABLE_INDEXERS=True makes the
        # notification publisher a no-op — the consumer starts but
        # the stream stays empty.
        from .services.indexer_consumer import indexer_consumer
        await indexer_consumer.start()

    # ── Clip queue worker ─────────────────────────────────────
    #
    # The clip queue is an in-process ``asyncio.Queue`` backed by a
    # periodic Mongo sweep. Multiple API workers running
    # ``recover_pending`` would double-claim the same bookmark ids
    # and run Playwright twice, so the queue MUST only start in
    # containers where ``ENABLE_CLIP_QUEUE`` is true — which is the
    # indexer sidecar in the multi-worker topology.
    if not _is_testing and settings.ENABLE_CLIP_QUEUE:
        from .services.clip_queue import clip_queue
        await clip_queue.start()
        recovered = await clip_queue.recover_pending()
        if recovered:
            logger.info("Clip queue: recovered %d pending bookmarks", recovered)

    # ── Error tracker worker (T4) ─────────────────────────────
    #
    # Consumes the ``errors:ingest`` Redis Stream, parses the raw
    # envelope payload, (T5) computes the fingerprint, upserts
    # the Issue row, and writes the event to the daily partition.
    # Lives on the same side as the clip queue: sidecar in the
    # multi-worker topology, API container otherwise.
    if not _is_testing and settings.ENABLE_ERROR_TRACKER_WORKER:
        from .services.error_tracker.counters import counter_flusher
        from .services.error_tracker.pipeline import install_real_handler
        from .services.error_tracker.worker import error_tracker_worker

        install_real_handler()
        await error_tracker_worker.start()
        await counter_flusher.start()

    # ── MCP server integration ────────────────────────────────
    #
    # The indexer sidecar (ENABLE_API=0) never serves MCP requests,
    # so skip the FastMCP mount entirely when the API surface is
    # disabled. ``event_store`` is still None-initialised below so
    # the shutdown path can check and skip its aclose().
    if settings.ENABLE_API:
        from .mcp.server import MCP_PATH, MOUNT_PREFIX, register_tools

        register_tools()

        # Register well-known routes (custom router: X-API-Key → 404, no key → OAuth metadata)
        from .mcp.well_known import router as well_known_router
        app.include_router(well_known_router)

        # OAuth consent screen router
        from .mcp.oauth_consent import router as mcp_consent_router
        app.include_router(mcp_consent_router)

        # Mount the stateless Redis-backed MCP transport. Handles both
        # /mcp and /mcp/ explicitly (no 307 redirects that strip auth
        # headers). Session state lives in Redis; any worker can serve
        # any request. See docs/architecture/mcp-stateless-transport.md
        from .mcp.transport import get_mcp_routes
        for route in get_mcp_routes(MOUNT_PREFIX):
            app.routes.insert(0, route)

        logger.info(
            "MCP server routes mounted at %s (Redis-backed stateless transport + OAuth)",
            MOUNT_PREFIX,
        )

    from .services.agent_manager import agent_manager as _agent_mgr
    try:
        yield
    finally:
        # Stop accepting new agent requests. New callers raise
        # AgentShuttingDownError immediately; in-flight callers
        # continue to completion.
        _agent_mgr.start_shutdown()

    # Shutdown
    # Wait for in-flight RPCs to finish. Long-running remote_exec
    # calls can take up to REMOTE_MAX_TIMEOUT_SECONDS, so this drain
    # is the only thing standing between a clean restart and a wave
    # of user-visible CommandTimeoutError responses.
    await _agent_mgr.drain(timeout=settings.AGENT_SHUTDOWN_DRAIN_TIMEOUT_SECONDS)
    # Stop the Redis bus listeners and drop our registry entries
    # AFTER the drain so any in-flight remote dispatch can still
    # publish its response to the bus.
    await _agent_mgr.stop()
    # Stop the chat manager subscriber. Outstanding chat fan-outs
    # at this point are best-effort; the subscriber loop will
    # gracefully exit on its next get_message tick.
    from .services.chat_manager import chat_manager as _chat_mgr
    await _chat_mgr.stop()
    # Stop the index notification consumer on the indexer sidecar.
    # In single-process mode this is a no-op (start() was never
    # called when ENABLE_INDEXERS=True and the ``indexer_consumer``
    # singleton still has ``_task is None``).
    if not _is_testing and settings.ENABLE_INDEXERS:
        from .services.indexer_consumer import indexer_consumer
        await indexer_consumer.stop()
    if not _is_testing:
        from .services.clip_queue import clip_queue
        await clip_queue.stop()
    if not _is_testing and settings.ENABLE_ERROR_TRACKER_WORKER:
        from .services.error_tracker.counters import counter_flusher
        from .services.error_tracker.worker import error_tracker_worker
        await counter_flusher.stop()
        await error_tracker_worker.stop()
    # Cancel flush_loop tasks and drain pending writes. The await
    # below should only ever raise CancelledError now that we have
    # cancelled the task — anything else is a real bug inside the
    # flush loop and must surface in operator logs instead of being
    # silently swallowed by ``except (Exception, BaseException): pass``.
    for _t in _flush_tasks:
        _t.cancel()
    for _t in _flush_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await _t
    for _idx in _flush_indexers:
        try:
            await asyncio.to_thread(_idx.flush)
        except Exception as _e:
            logger.warning("Final flush failed: %s", _e)
    if settings.ENABLE_API:
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

    # Capture to our own error tracker (direct Redis enqueue — no HTTP round-trip).
    # Skip client-side HTTP errors (4xx) — those are expected, not bugs.
    if not isinstance(exc, HTTPException) or exc.status_code >= 500:
        try:
            from .services.error_tracker.capture import capture_exception
            await capture_exception(
                exc,
                extra={"path": str(request.url.path), "method": request.method},
            )
        except Exception:
            logger.exception("error-tracker capture: unexpected failure in handler")

    return ORJSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# Routers
#
# The indexer sidecar (``ENABLE_API=0``) skips every router mount,
# so that container only exposes ``/health`` (defined below) and
# does not accidentally answer HTTP API requests it cannot safely
# serve (e.g. a write that would need to notify itself via Redis).
if settings.ENABLE_API:
    from .api.v1.endpoints import attachments, auth, backup, bookmark_assets, bookmarks, chat, docsites, documents, error_tracker as error_tracker_api, events, knowledge, mcp_keys, mcp_usage, projects, public_config, secrets, tasks, users, workspaces  # noqa: E402

    app.include_router(public_config.router, prefix="/api/v1")
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
    app.include_router(secrets.router, prefix="/api/v1")
    app.include_router(error_tracker_api.router, prefix="/api/v1")

    # Sentry-compatible envelope ingest. Mounted at root (no /api/v1
    # prefix) because the Sentry SDK DSN format expects
    # ``POST /api/{project_id}/envelope/`` — see spec §3.1.
    from .api.error_tracker_ingest import router as error_tracker_ingest_router  # noqa: E402
    app.include_router(error_tracker_ingest_router)


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
