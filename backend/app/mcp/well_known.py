"""MCP 末尾スラッシュ補正ミドルウェア。

well-known ルートは TodoOAuthProvider.get_routes() / get_well_known_routes() が
FastMCP フレームワーク経由で自動生成する。手動登録は不要。
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from .server import MOUNT_PREFIX


class McpTrailingSlashMiddleware:
    """Rewrite /mcp (no trailing slash) to /mcp/ internally.

    Starlette's Mount matches /mcp/ but returns 307 for /mcp.
    Claude Desktop drops Authorization headers on 307 redirects.
    """

    def __init__(self, wrapped_app: ASGIApp):
        self.app = wrapped_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("path") == MOUNT_PREFIX:
            scope = dict(scope)
            scope["path"] = MOUNT_PREFIX + "/"
        await self.app(scope, receive, send)
