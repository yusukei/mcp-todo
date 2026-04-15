"""Data types used by the MCP OAuth provider mixins.

``TodoAuthorizationCode`` carries the user_id that the consent flow
embeds for later claim extraction. ``DualAuthBackend`` is a Starlette
authentication backend that lets Bearer tokens and X-API-Key headers
both pass through the auth middleware.
"""
from __future__ import annotations

from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser,
    BearerAuthBackend,
)
from mcp.server.auth.provider import AuthorizationCode
from starlette.authentication import AuthCredentials
from starlette.requests import HTTPConnection


class TodoAuthorizationCode(AuthorizationCode):
    """AuthorizationCode にユーザー ID を追加"""

    user_id: str


# X-API-Key リクエスト用のプレースホルダ AccessToken
# RequireAuthMiddleware を通過させるためだけに使う。
# 実際の認証・認可はツールレベルの authenticate() で行う。
_API_KEY_PLACEHOLDER_TOKEN = AccessToken(
    token="__api_key_passthrough__",  # noqa: S106
    client_id="api-key-client",
    scopes=[],
    expires_at=None,
)


class DualAuthBackend(BearerAuthBackend):
    """Bearer トークンと X-API-Key の両方を受け付ける認証バックエンド。

    Bearer トークンがあれば通常の OAuth 検証を行う。
    X-API-Key ヘッダーがあれば、プレースホルダ AccessToken で
    ミドルウェアを通過させ、ツールレベルで本認証を行う。
    """

    async def authenticate(self, conn: HTTPConnection):
        # 1. Bearer トークンを優先
        result = await super().authenticate(conn)
        if result is not None:
            return result

        # 2. X-API-Key — DB で検証してから通過させる
        api_key = conn.headers.get("x-api-key")
        if api_key and api_key.strip():
            from ..auth import McpAuthError, _resolve_api_key_user

            try:
                await _resolve_api_key_user(api_key)
            except McpAuthError:
                return None
            return AuthCredentials([]), AuthenticatedUser(_API_KEY_PLACEHOLDER_TOKEN)

        return None
