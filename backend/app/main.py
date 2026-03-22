import logging
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

if settings.SECRET_KEY == "change-me":
    print("FATAL: SECRET_KEY is not set", file=sys.stderr)
    sys.exit(1)

if settings.REFRESH_SECRET_KEY == "change-me-refresh":
    print("FATAL: REFRESH_SECRET_KEY is not set", file=sys.stderr)
    sys.exit(1)

if settings.MCP_INTERNAL_SECRET == "change-me":
    print("FATAL: MCP_INTERNAL_SECRET is not set", file=sys.stderr)
    sys.exit(1)


class ORJSONResponse(JSONResponse):
    def render(self, content: object) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_NAIVE_UTC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    yield
    await close_redis()
    await close_db()


app = FastAPI(
    title="Claude Todo API",
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
# Routers
from .api.v1.endpoints import auth, events, internal, mcp_keys, projects, tasks, users

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(mcp_keys.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(internal.router, prefix="/api/v1")


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
