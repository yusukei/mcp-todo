import logging
import sys
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .core.config import settings
from .core.database import connect
from .core.redis import close_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

if settings.SECRET_KEY == "change-me":
    print("FATAL: SECRET_KEY is not set", file=sys.stderr)
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
    allow_methods=["*"],
    allow_headers=["*"],
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
async def health() -> dict:
    return {"status": "ok"}
