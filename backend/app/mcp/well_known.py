"""MCP well-known routes and trailing slash middleware.

Registers /.well-known/oauth-protected-resource and
/.well-known/oauth-authorization-server manually to avoid
conflicts with FastMCP's auto-registered routes.
"""

from __future__ import annotations

from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as StarletteJSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..core.config import settings
from .server import MOUNT_PREFIX

# ---------------------------------------------------------------------------
# OAuth well-known discovery (static JSON)
# ---------------------------------------------------------------------------

# MCP Todo uses API key auth, not OAuth.
# These endpoints return minimal metadata so MCP clients
# that probe /.well-known don't get 404s.


def _get_base_url(request: StarletteRequest) -> str:
    """Build base URL from config or request headers."""
    if settings.BASE_URL:
        return settings.BASE_URL.rstrip("/") + MOUNT_PREFIX
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", "localhost")
    return f"{scheme}://{host}{MOUNT_PREFIX}"


async def _well_known_protected_resource(request: StarletteRequest):
    base_url = _get_base_url(request)
    return StarletteJSONResponse({
        "resource": base_url + "/",
        "bearer_methods_supported": ["header"],
    })


async def _well_known_auth_server(request: StarletteRequest):
    base_url = _get_base_url(request)
    return StarletteJSONResponse({
        "issuer": base_url + "/",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


def get_well_known_routes() -> list[Route]:
    """Return well-known routes for all client path variations."""
    suffixes = ["", "/", MOUNT_PREFIX, MOUNT_PREFIX + "/"]
    routes: list[Route] = []
    for suffix in suffixes:
        routes.append(
            Route(f"/.well-known/oauth-protected-resource{suffix}", _well_known_protected_resource)
        )
        routes.append(
            Route(f"/.well-known/oauth-authorization-server{suffix}", _well_known_auth_server)
        )
    return routes


# ---------------------------------------------------------------------------
# Trailing slash rewrite middleware
# ---------------------------------------------------------------------------


class McpTrailingSlashMiddleware:
    """Rewrite /mcp (no trailing slash) to /mcp/ internally.

    Starlette's Mount matches /mcp/ but returns 307 for /mcp.
    Claude Code drops Authorization headers on 307 redirects.
    """

    def __init__(self, wrapped_app: ASGIApp):
        self.app = wrapped_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("path") == MOUNT_PREFIX:
            scope = dict(scope)
            scope["path"] = MOUNT_PREFIX + "/"
        await self.app(scope, receive, send)
