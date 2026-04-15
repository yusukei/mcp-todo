"""MCP 末尾スラッシュ補正ミドルウェア + well-known ルーター。

/.well-known/oauth-* ルートを FastAPI の APIRouter として再実装する。
X-API-Key ヘッダーが存在する場合は 404 を返す（APIキー利用者に OAuth を見せない）。
X-API-Key が存在しない場合は RFC 8414 / RFC 9728 準拠のメタデータ JSON を返す。

FastMCP の get_well_known_routes() は使用せず、このルーターを main.py で登録する。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .server import MOUNT_PREFIX, _base_url


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


# ── well-known router ────────────────────────────────────────────────────────

router = APIRouter()

_base = _base_url.rstrip("/")

_as_metadata = {
    "issuer": _base,
    "authorization_endpoint": f"{_base}/authorize",
    "token_endpoint": f"{_base}/token",
    "registration_endpoint": f"{_base}/register",
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_methods_supported": ["none"],
    "code_challenge_methods_supported": ["S256"],
}

_prm_metadata = {
    "resource": _base,
    "authorization_servers": [_base],
    "bearer_methods_supported": ["header"],
}


def _has_api_key(request: Request) -> bool:
    return "x-api-key" in request.headers


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/")
@router.get("/.well-known/oauth-authorization-server/mcp")
@router.get("/.well-known/oauth-authorization-server/mcp/")
async def as_metadata(request: Request) -> JSONResponse:
    if _has_api_key(request):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return JSONResponse(_as_metadata)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/")
@router.get("/.well-known/oauth-protected-resource/mcp")
@router.get("/.well-known/oauth-protected-resource/mcp/")
async def prm_metadata(request: Request) -> JSONResponse:
    if _has_api_key(request):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return JSONResponse(_prm_metadata)
