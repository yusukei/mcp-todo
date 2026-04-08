"""MCP Todo custom OAuth provider package (Redis-backed).

Splits the former 636-line ``oauth_provider.py`` into focused modules
composed via cooperative mixins:

    class TodoOAuthProvider(
        ClientsMixin,       # get_client, register_client (RFC 7591)
        AuthorizationMixin, # authorize, {store,load}_authorization_code
        TokenMixin,         # exchange_*, load_*, revoke_token
        _BaseProvider,      # OAuthProvider subclass: __init__, middleware,
                            #   discovery routes, Redis helpers
    )

Public re-exports keep ``from app.mcp.oauth import ...`` as the single
import path for external consumers (server.py, main.py, oauth_consent.py,
tests). Backwards-compat names from the pre-split module are preserved:

- ``TodoOAuthProvider``   — the composed provider
- ``TodoAuthorizationCode`` — user_id-bearing auth code
- ``DualAuthBackend``     — Starlette backend that accepts Bearer or X-API-Key
- ``PENDING_AUTH_PREFIX`` — Redis key prefix for pending consent flow
- ``get_mcp_redis`` / ``close_mcp_redis`` — Redis singleton lifecycle
"""
from __future__ import annotations

from ._authorization import AuthorizationMixin
from ._base import _BaseProvider
from ._clients import ClientsMixin
from ._models import DualAuthBackend, TodoAuthorizationCode
from ._redis import (
    PENDING_AUTH_PREFIX,
    PENDING_AUTH_TTL,
    close_mcp_redis,
    get_mcp_redis,
)
from ._tokens import TokenMixin


class TodoOAuthProvider(
    ClientsMixin,
    AuthorizationMixin,
    TokenMixin,
    _BaseProvider,
):
    """MCP Todo OAuth プロバイダ（Redis バックエンド）

    authorize() で auth code を直接発行せず、pending auth を Redis に保存して
    同意画面にリダイレクトする。同意画面でユーザーが許可すると、
    oauth_consent.py が TodoAuthorizationCode を作成する。
    """


__all__ = [
    "TodoOAuthProvider",
    "TodoAuthorizationCode",
    "DualAuthBackend",
    "PENDING_AUTH_PREFIX",
    "PENDING_AUTH_TTL",
    "get_mcp_redis",
    "close_mcp_redis",
]
