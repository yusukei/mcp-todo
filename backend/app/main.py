import logging
import os
import sys
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .core.config import settings
from .core.database import close_db, connect, get_mongo_client
from .core.redis import close_redis, get_redis

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

    # ── MCP server integration ────────────────────────────────
    from .mcp.server import MCP_PATH, MOUNT_PREFIX, register_tools
    from .mcp.server import mcp as _mcp_server

    register_tools()

    # Inject ResilientSessionManager for container restart recovery
    import fastmcp.server.http as _fmcp_http
    from .mcp.session_manager import ResilientSessionManager
    _orig_manager = _fmcp_http.StreamableHTTPSessionManager
    _fmcp_http.StreamableHTTPSessionManager = ResilientSessionManager  # type: ignore[misc]

    from .mcp.session_store import RedisEventStore
    event_store = RedisEventStore()
    _mcp_app = _mcp_server.http_app(path=MCP_PATH, event_store=event_store)

    _fmcp_http.StreamableHTTPSessionManager = _orig_manager  # restore

    # Register well-known routes at root level (before MCP mount)
    from .mcp.well_known import get_well_known_routes
    for route in get_well_known_routes():
        app.routes.insert(0, route)

    # Mount MCP subapp
    app.mount(MOUNT_PREFIX, _mcp_app)
    logger.info("MCP server mounted at %s (stateful + RedisEventStore)", MOUNT_PREFIX)

    # MCP subapp lifespan (Starlette mount doesn't auto-execute subapp lifespan)
    _is_testing = os.environ.get("TESTING") == "1"
    if not _is_testing:
        async with _mcp_app.lifespan(_mcp_app):
            yield
    else:
        yield

    # Shutdown
    await event_store.aclose()
    await close_redis()
    await close_db()


app = FastAPI(
    title="Claude Todo API",
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

# Routers
from .api.v1.endpoints import auth, events, mcp_keys, projects, tasks, users  # noqa: E402

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(mcp_keys.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")


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
