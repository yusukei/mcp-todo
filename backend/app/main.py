import logging
import os
import sys
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Request
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

if settings.SECRET_KEY == "change-me":
    print("FATAL: SECRET_KEY is not set", file=sys.stderr)
    sys.exit(1)

if settings.REFRESH_SECRET_KEY == "change-me-refresh":
    print("FATAL: REFRESH_SECRET_KEY is not set", file=sys.stderr)
    sys.exit(1)


class ORJSONResponse(JSONResponse):
    def render(self, content: object) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_NAIVE_UTC)


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

    # ── Search index initialization ─────────────────────────
    _is_testing = os.environ.get("TESTING") == "1"
    if not _is_testing:
        from .services.search import TANTIVY_AVAILABLE
        if TANTIVY_AVAILABLE:
            try:
                from pathlib import Path
                from .services.search import SearchIndex, SearchIndexer, SearchService
                search_index = SearchIndex(Path(settings.SEARCH_INDEX_DIR))
                indexer = SearchIndexer(search_index)
                SearchIndexer.set_instance(indexer)
                SearchService.set_instance(SearchService(search_index))
                count = await indexer.rebuild()
                logger.info("Search index ready: %d tasks indexed", count)
            except Exception as e:
                logger.warning("Failed to initialize search index: %s — full-text search disabled", e)
        else:
            logger.info("tantivy not available — full-text search disabled (using $regex fallback)")

    # ── Knowledge search index initialization ─────────────
    if not _is_testing:
        from .services.knowledge_search import TANTIVY_AVAILABLE as K_TANTIVY
        if K_TANTIVY:
            try:
                from pathlib import Path as _Path
                from .services.knowledge_search import (
                    KnowledgeSearchIndex, KnowledgeSearchIndexer, KnowledgeSearchService,
                )
                k_index = KnowledgeSearchIndex(_Path(settings.KNOWLEDGE_INDEX_DIR))
                k_indexer = KnowledgeSearchIndexer(k_index)
                KnowledgeSearchIndexer.set_instance(k_indexer)
                KnowledgeSearchService.set_instance(KnowledgeSearchService(k_index))
                k_count = await k_indexer.rebuild()
                logger.info("Knowledge search index ready: %d entries indexed", k_count)
            except Exception as e:
                logger.warning("Failed to initialize knowledge search index: %s", e)

    # ── Document search index initialization ────────────────
    if not _is_testing:
        from .services.document_search import TANTIVY_AVAILABLE as D_TANTIVY
        if D_TANTIVY:
            try:
                from pathlib import Path as _DPath
                from .services.document_search import (
                    DocumentSearchIndex, DocumentSearchIndexer, DocumentSearchService,
                )
                d_index = DocumentSearchIndex(_DPath(settings.DOCUMENT_INDEX_DIR))
                d_indexer = DocumentSearchIndexer(d_index)
                DocumentSearchIndexer.set_instance(d_indexer)
                DocumentSearchService.set_instance(DocumentSearchService(d_index))
                d_count = await d_indexer.rebuild()
                logger.info("Document search index ready: %d entries indexed", d_count)
            except Exception as e:
                logger.warning("Failed to initialize document search index: %s", e)

    # ── DocSite search index initialization ──────────────────
    if not _is_testing:
        from .services.docsite_search import TANTIVY_AVAILABLE as DS_TANTIVY
        if DS_TANTIVY:
            try:
                from pathlib import Path as _DSPath
                from .services.docsite_search import (
                    DocSiteSearchIndex, DocSiteSearchIndexer, DocSiteSearchService,
                )
                ds_index = DocSiteSearchIndex(_DSPath(settings.DOCSITE_INDEX_DIR))
                ds_indexer = DocSiteSearchIndexer(ds_index)
                DocSiteSearchIndexer.set_instance(ds_indexer)
                DocSiteSearchService.set_instance(DocSiteSearchService(ds_index))
                ds_count = await ds_indexer.rebuild()
                logger.info("DocSite search index ready: %d pages indexed", ds_count)
            except Exception as e:
                logger.warning("Failed to initialize docsite search index: %s", e)

    # ── Bookmark search index initialization ──────────────────
    if not _is_testing:
        from .services.bookmark_search import TANTIVY_AVAILABLE as BM_TANTIVY
        if BM_TANTIVY:
            try:
                from pathlib import Path as _BMPath
                from .services.bookmark_search import (
                    BookmarkSearchIndex, BookmarkSearchIndexer, BookmarkSearchService,
                )
                bm_index = BookmarkSearchIndex(_BMPath(settings.BOOKMARK_INDEX_DIR))
                bm_indexer = BookmarkSearchIndexer(bm_index)
                BookmarkSearchIndexer.set_instance(bm_indexer)
                BookmarkSearchService.set_instance(BookmarkSearchService(bm_index))
                bm_count = await bm_indexer.rebuild()
                logger.info("Bookmark search index ready: %d bookmarks indexed", bm_count)
            except Exception as e:
                logger.warning("Failed to initialize bookmark search index: %s", e)

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

    # Inject ResilientSessionManager for container restart recovery
    # FastMCP.http_app() は auth= を渡せないため、create_streamable_http_app() を直接呼び出す
    import fastmcp.server.http as _fmcp_http
    from .mcp.session_manager import ResilientSessionManager
    _orig_manager = _fmcp_http.StreamableHTTPSessionManager
    _fmcp_http.StreamableHTTPSessionManager = ResilientSessionManager  # type: ignore[misc]

    from .mcp.session_store import RedisEventStore
    event_store = RedisEventStore()
    _mcp_app = _fmcp_http.create_streamable_http_app(
        server=_mcp_server,
        streamable_http_path=MCP_PATH,
        event_store=event_store,
        auth=_mcp_server.auth,
    )

    _fmcp_http.StreamableHTTPSessionManager = _orig_manager  # restore

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
    if not _is_testing:
        async with _mcp_app.lifespan(_mcp_app):
            yield
    else:
        yield

    # Shutdown
    if not _is_testing:
        from .services.clip_queue import clip_queue
        await clip_queue.stop()
    await event_store.aclose()
    from .mcp.oauth_provider import close_mcp_redis
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
from .api.v1.endpoints import attachments, auth, backup, bookmark_assets, bookmarks, docsites, documents, events, knowledge, mcp_keys, projects, tasks, terminal, users  # noqa: E402

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
app.include_router(terminal.router, prefix="/api/v1")


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
