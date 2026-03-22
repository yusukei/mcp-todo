"""MCP スタンドアロンサーバ。

aquarium4.5 の standalone.py パターンを参考に実装。
- FastMCP を /mcp にマウント（stateful、RedisEventStore）
- McpTrailingSlashMiddleware（307でAuthヘッダーが落ちる問題対策）
- X-API-Key 認証（OAuth不使用のため /.well-known 不要）
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import close_client
from .server import MOUNT_PREFIX, MCP_PATH, mcp, register_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


class McpTrailingSlashMiddleware:
    """/mcp（末尾スラッシュなし）を /mcp/ に内部リライトする。

    Starlette の Mount は /mcp/ にマッチするが /mcp は 307 になり、
    Claude Code が 307 で Authorization ヘッダーを落とすため必要。
    """

    def __init__(self, wrapped_app: ASGIApp) -> None:
        self.app = wrapped_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == MOUNT_PREFIX:
            scope = dict(scope)
            scope["path"] = MOUNT_PREFIX + "/"
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    register_tools()

    # RedisEventStore を注入して stateful FastMCP を起動
    from .session_store import RedisEventStore
    event_store = RedisEventStore()
    _mcp_app = mcp.http_app(path=MCP_PATH, event_store=event_store)

    app.mount(MOUNT_PREFIX, _mcp_app)
    logger.info("MCP server mounted at %s (stateful + RedisEventStore)", MOUNT_PREFIX)

    async with _mcp_app.lifespan(_mcp_app):
        yield

    await close_client()
    await event_store.aclose()
    logger.info("MCP server stopped")


app = FastAPI(title="Claude Todo MCP Server", lifespan=lifespan)
app.add_middleware(McpTrailingSlashMiddleware)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
