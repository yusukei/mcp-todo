"""MCP Todo カスタム OAuth プロバイダ（Redis ストレージ）

OAuthProvider を直接継承し、全ストアを Redis に保持する。
サーバ再起動後もクライアント登録・アクセストークン・リフレッシュトークンが維持される。
authorize() で同意画面にリダイレクトし、ユーザーの user_id を AuthorizationCode に埋め込む。
exchange_authorization_code() で AccessToken.claims に user_id を格納する。
"""

from __future__ import annotations

import json
import logging
import secrets
import time

import redis.asyncio as aioredis
from fastmcp.server.auth.auth import AccessToken, OAuthProvider
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser,
    BearerAuthBackend,
)
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.authentication import AuthCredentials
from starlette.requests import HTTPConnection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis シングルトン（MCP 用 DB1）— oauth_consent.py からも参照さ��る
# ---------------------------------------------------------------------------

_mcp_redis: aioredis.Redis | None = None


def get_mcp_redis() -> aioredis.Redis:
    """MCP 用 Redis クライアントを返す（シングルトン）。"""
    global _mcp_redis
    if _mcp_redis is None:
        from ..core.config import settings
        _mcp_redis = aioredis.from_url(settings.REDIS_MCP_URI, decode_responses=True)
    return _mcp_redis


async def close_mcp_redis() -> None:
    """シャットダウン時に呼ぶ。"""
    global _mcp_redis
    if _mcp_redis is not None:
        await _mcp_redis.aclose()
        _mcp_redis = None


# Redis キープレフィックス
PENDING_AUTH_PREFIX = "todo:mcp:pending_auth:"
PENDING_AUTH_TTL = 600  # 10 分

_KEY_CLIENT = "todo:mcp:client:"
_KEY_AUTH_CODE = "todo:mcp:auth_code:"
_KEY_ACCESS_TOKEN = "todo:mcp:access_token:"  # noqa: S105
_KEY_REFRESH_TOKEN = "todo:mcp:refresh_token:"  # noqa: S105
_KEY_A2R = "todo:mcp:a2r:"  # access_token -> refresh_token
_KEY_R2A = "todo:mcp:r2a:"  # refresh_token -> access_token

# デフォルト有効期限（秒）
DEFAULT_AUTH_CODE_EXPIRY = 5 * 60  # 5 分
DEFAULT_ACCESS_TOKEN_EXPIRY = 7 * 24 * 60 * 60  # 7 日
DEFAULT_REFRESH_TOKEN_EXPIRY: int | None = None  # 無期限
# 無期限トークンの Redis TTL（30 日）
_FALLBACK_TTL = 30 * 24 * 60 * 60


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

        # 2. X-API-Key フォールバック — ミドルウェアを通過させる
        api_key = conn.headers.get("x-api-key")
        if api_key and api_key.strip():
            return AuthCredentials([]), AuthenticatedUser(_API_KEY_PLACEHOLDER_TOKEN)

        return None


class TodoOAuthProvider(OAuthProvider):
    """MCP Todo OAuth プロバイダ（Redis バックエンド）

    authorize() で auth code を直接発行せず、pending auth を Redis に保存して
    同意画面にリダイレクトする。同意画面でユーザーが許可すると、
    oauth_consent.py が TodoAuthorizationCode を作成する。
    """

    def __init__(
        self,
        base_url: AnyHttpUrl | str,
        client_registration_options: ClientRegistrationOptions | None = None,
        revocation_options: RevocationOptions | None = None,
        required_scopes: list[str] | None = None,
    ):
        super().__init__(
            base_url=base_url,
            client_registration_options=client_registration_options,
            revocation_options=revocation_options,
            required_scopes=required_scopes,
        )

    def get_middleware(self) -> list:
        """DualAuthBackend を使って Bearer + X-API-Key の両方をサポート。"""
        from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
        from starlette.middleware import Middleware
        from starlette.middleware.authentication import AuthenticationMiddleware

        return [
            Middleware(
                AuthenticationMiddleware,
                backend=DualAuthBackend(self),
            ),
            Middleware(AuthContextMiddleware),
        ]

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """AS メタデータの token_endpoint_auth_methods_supported に 'none' を追加。

        FastMCP/MCP SDK が生成するデフォルトメタデータには 'none' が含まれず、
        public client (Claude Desktop 等) が認証方式を判定できない。
        AS metadata ルートを自前のエンドポイントで差し替える。
        """
        from starlette.responses import JSONResponse as _JSON
        from starlette.routing import Route as _Route

        routes = super().get_routes(mcp_path)

        # AS metadata を自前で構築（aquarium4.5 と同じ形式）
        # issuer は末尾スラッシュ付き（RFC 8414 issuer 検証との整合性）
        base = str(self.base_url).rstrip("/")
        as_metadata = {
            "issuer": base + "/",
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
        }

        async def _as_metadata_endpoint(request):
            return _JSON(as_metadata)

        # PRM (Protected Resource Metadata) も自前で構築
        prm_metadata = {
            "resource": base + "/",
            "authorization_servers": [base + "/"],
            "bearer_methods_supported": ["header"],
        }

        async def _prm_endpoint(request):
            return _JSON(prm_metadata)

        # AS metadata と PRM ルートを差し替え
        patched: list = []
        for route in routes:
            if isinstance(route, _Route) and "oauth-authorization-server" in route.path:
                patched.append(
                    _Route(route.path, endpoint=_as_metadata_endpoint, methods=route.methods)
                )
            elif isinstance(route, _Route) and "oauth-protected-resource" in route.path:
                patched.append(
                    _Route(route.path, endpoint=_prm_endpoint, methods=route.methods)
                )
            else:
                patched.append(route)

        return patched

    def get_well_known_routes(self, mcp_path: str | None = None) -> list[Route]:
        """ルートレベル + path-aware の両方の well-known ルートを返す。

        FastMCP のデフォルトは path-aware のみ（/.well-known/.../mcp）だが、
        2025-03-26 仕様のクライアントはルートレベル（/.well-known/...）にもアクセスする。
        両パターンを登録する。
        """
        from starlette.routing import Route as _Route

        routes = super().get_well_known_routes(mcp_path)

        existing_paths = {r.path for r in routes if isinstance(r, _Route)}

        for route in list(routes):
            if not isinstance(route, _Route):
                continue
            for wk in ("oauth-authorization-server", "oauth-protected-resource"):
                if wk in route.path:
                    for suffix in (f"/.well-known/{wk}", f"/.well-known/{wk}/"):
                        if suffix not in existing_paths:
                            routes.append(
                                _Route(suffix, endpoint=route.endpoint, methods=route.methods)
                            )
                            existing_paths.add(suffix)

        return routes

    # ------------------------------------------------------------------
    # Redis ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _get_redis() -> aioredis.Redis:
        return get_mcp_redis()

    async def _redis_set(self, key: str, value: str, ttl: int | None = None) -> None:
        r = self._get_redis()
        if ttl and ttl > 0:
            await r.set(key, value, ex=ttl)
        else:
            await r.set(key, value, ex=_FALLBACK_TTL)

    async def _redis_get(self, key: str) -> str | None:
        r = self._get_redis()
        return await r.get(key)

    async def _redis_del(self, *keys: str) -> None:
        if not keys:
            return
        r = self._get_redis()
        await r.delete(*keys)

    @staticmethod
    def _ttl_from_expires(expires_at: float | int | None) -> int | None:
        if expires_at is None:
            return None
        remaining = int(expires_at - time.time())
        return max(remaining, 1)

    # ------------------------------------------------------------------
    # Client Registration
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = await self._redis_get(f"{_KEY_CLIENT}{client_id}")
        if raw is None:
            return None
        return OAuthClientInformationFull.model_validate_json(raw)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        logger.info("MCP OAuth: client registration: %s", client_info.client_name)
        if (
            client_info.scope is not None
            and self.client_registration_options is not None
            and self.client_registration_options.valid_scopes is not None
        ):
            requested_scopes = set(client_info.scope.split())
            valid_scopes = set(self.client_registration_options.valid_scopes)
            invalid_scopes = requested_scopes - valid_scopes
            if invalid_scopes:
                raise ValueError(
                    f"Requested scopes are not valid: {', '.join(invalid_scopes)}"
                )

        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")

        await self._redis_set(
            f"{_KEY_CLIENT}{client_info.client_id}",
            client_info.model_dump_json(),
        )

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        logger.info("MCP OAuth: authorize request from client=%s", client.client_id)
        stored = await self.get_client(client.client_id or "")
        if stored is None:
            from mcp.server.auth.provider import AuthorizeError

            raise AuthorizeError(
                error="unauthorized_client",
                error_description=f"Client '{client.client_id}' not registered.",
            )

        pending_id = secrets.token_urlsafe(32)
        pending_data = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "state": params.state,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "resource": params.resource,
        }

        r = self._get_redis()
        await r.set(
            f"{PENDING_AUTH_PREFIX}{pending_id}",
            json.dumps(pending_data),
            ex=PENDING_AUTH_TTL,
        )

        # base_url は "https://host/mcp" 形式。ホスト部分を抽出して同意画面 URL を構築
        base_str = str(self.base_url).rstrip("/")
        # MOUNT_PREFIX ("/mcp") を除去してホスト URL を得る
        from .server import MOUNT_PREFIX
        if base_str.endswith(MOUNT_PREFIX):
            host_url = base_str[: -len(MOUNT_PREFIX)]
        else:
            host_url = base_str
        return f"{host_url}/api/v1/mcp/oauth/consent?pending={pending_id}"

    # ------------------------------------------------------------------
    # Authorization Code
    # ------------------------------------------------------------------

    async def store_authorization_code(self, code: AuthorizationCode) -> None:
        ttl = self._ttl_from_expires(code.expires_at)
        data = code.model_dump_json()
        if isinstance(code, TodoAuthorizationCode):
            raw = json.loads(data)
            raw["_type"] = "todo"
            data = json.dumps(raw)
        await self._redis_set(f"{_KEY_AUTH_CODE}{code.code}", data, ttl)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = await self._redis_get(f"{_KEY_AUTH_CODE}{authorization_code}")
        if raw is None:
            return None

        data = json.loads(raw)
        is_todo = data.pop("_type", None) == "todo"

        if is_todo:
            code_obj = TodoAuthorizationCode.model_validate(data)
        else:
            code_obj = AuthorizationCode.model_validate(data)

        if code_obj.client_id != client.client_id:
            return None
        if code_obj.expires_at < time.time():
            await self._redis_del(f"{_KEY_AUTH_CODE}{authorization_code}")
            return None
        return code_obj

    # ------------------------------------------------------------------
    # Token Exchange
    # ------------------------------------------------------------------

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        logger.info("MCP OAuth: token exchange for client=%s", client.client_id)
        # getdel でアトミックに取得+削除（TOCTOU 防止）
        r = self._get_redis()
        raw = await r.getdel(f"{_KEY_AUTH_CODE}{authorization_code.code}")
        if raw is None:
            from mcp.server.auth.provider import TokenError

            raise TokenError(
                "invalid_grant", "Authorization code not found or already used."
            )

        user_id = getattr(authorization_code, "user_id", None)

        access_token_value = f"todo_at_{secrets.token_hex(32)}"
        refresh_token_value = f"todo_rt_{secrets.token_hex(32)}"
        access_expires_at = int(time.time() + DEFAULT_ACCESS_TOKEN_EXPIRY)

        refresh_expires_at = None
        if DEFAULT_REFRESH_TOKEN_EXPIRY is not None:
            refresh_expires_at = int(time.time() + DEFAULT_REFRESH_TOKEN_EXPIRY)

        if client.client_id is None:
            from mcp.server.auth.provider import TokenError

            raise TokenError("invalid_client", "Client ID is required")

        claims = {"user_id": user_id} if user_id else {}

        access_token = AccessToken(
            token=access_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=access_expires_at,
            claims=claims,
        )
        refresh_token = RefreshToken(
            token=refresh_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=refresh_expires_at,
        )

        at_ttl = self._ttl_from_expires(access_expires_at)
        rt_ttl = self._ttl_from_expires(refresh_expires_at)

        r = self._get_redis()
        pipe = r.pipeline()
        pipe.set(
            f"{_KEY_ACCESS_TOKEN}{access_token_value}",
            access_token.model_dump_json(),
            ex=at_ttl or _FALLBACK_TTL,
        )
        pipe.set(
            f"{_KEY_REFRESH_TOKEN}{refresh_token_value}",
            refresh_token.model_dump_json(),
            ex=rt_ttl or _FALLBACK_TTL,
        )
        pipe.set(
            f"{_KEY_A2R}{access_token_value}",
            refresh_token_value,
            ex=rt_ttl or _FALLBACK_TTL,
        )
        pipe.set(
            f"{_KEY_R2A}{refresh_token_value}",
            access_token_value,
            ex=at_ttl or _FALLBACK_TTL,
        )
        await pipe.execute()

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",  # noqa: S106
            expires_in=DEFAULT_ACCESS_TOKEN_EXPIRY,
            refresh_token=refresh_token_value,
            scope=" ".join(authorization_code.scopes) or None,
        )

    # ------------------------------------------------------------------
    # Access Token
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        raw = await self._redis_get(f"{_KEY_ACCESS_TOKEN}{token}")
        if raw is None:
            return None

        token_obj = AccessToken.model_validate_json(raw)
        if token_obj.expires_at is not None and token_obj.expires_at < time.time():
            await self._revoke_internal(access_token_str=token)
            return None
        return token_obj

    # ------------------------------------------------------------------
    # Refresh Token
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        raw = await self._redis_get(f"{_KEY_REFRESH_TOKEN}{refresh_token}")
        if raw is None:
            return None

        token_obj = RefreshToken.model_validate_json(raw)
        if token_obj.client_id != client.client_id:
            return None
        if token_obj.expires_at is not None and token_obj.expires_at < time.time():
            await self._revoke_internal(refresh_token_str=token_obj.token)
            return None
        return token_obj

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        original_scopes = set(refresh_token.scopes)
        requested_scopes = set(scopes)
        if not requested_scopes.issubset(original_scopes):
            from mcp.server.auth.provider import TokenError

            raise TokenError(
                "invalid_scope",
                "Requested scopes exceed those authorized by the refresh token.",
            )

        # 旧 access token から user_id を取得
        old_user_id = None
        old_access_str = await self._redis_get(f"{_KEY_R2A}{refresh_token.token}")
        if old_access_str:
            old_access_raw = await self._redis_get(
                f"{_KEY_ACCESS_TOKEN}{old_access_str}"
            )
            if old_access_raw:
                old_access = AccessToken.model_validate_json(old_access_raw)
                if hasattr(old_access, "claims") and old_access.claims:
                    old_user_id = old_access.claims.get("user_id")

        await self._revoke_internal(refresh_token_str=refresh_token.token)

        # ユーザーの有効性を再検証
        if old_user_id:
            from ..models import User

            user = await User.get(old_user_id)
            if not user or not user.is_active:
                from mcp.server.auth.provider import TokenError

                raise TokenError("invalid_grant", "User is disabled")

        new_access_value = f"todo_at_{secrets.token_hex(32)}"
        new_refresh_value = f"todo_rt_{secrets.token_hex(32)}"
        access_expires_at = int(time.time() + DEFAULT_ACCESS_TOKEN_EXPIRY)

        refresh_expires_at = None
        if DEFAULT_REFRESH_TOKEN_EXPIRY is not None:
            refresh_expires_at = int(time.time() + DEFAULT_REFRESH_TOKEN_EXPIRY)

        if client.client_id is None:
            from mcp.server.auth.provider import TokenError

            raise TokenError("invalid_client", "Client ID is required")

        claims = {"user_id": old_user_id} if old_user_id else {}

        new_access = AccessToken(
            token=new_access_value,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=access_expires_at,
            claims=claims,
        )
        new_refresh = RefreshToken(
            token=new_refresh_value,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=refresh_expires_at,
        )

        at_ttl = self._ttl_from_expires(access_expires_at)
        rt_ttl = self._ttl_from_expires(refresh_expires_at)

        r = self._get_redis()
        pipe = r.pipeline()
        pipe.set(
            f"{_KEY_ACCESS_TOKEN}{new_access_value}",
            new_access.model_dump_json(),
            ex=at_ttl or _FALLBACK_TTL,
        )
        pipe.set(
            f"{_KEY_REFRESH_TOKEN}{new_refresh_value}",
            new_refresh.model_dump_json(),
            ex=rt_ttl or _FALLBACK_TTL,
        )
        pipe.set(
            f"{_KEY_A2R}{new_access_value}",
            new_refresh_value,
            ex=rt_ttl or _FALLBACK_TTL,
        )
        pipe.set(
            f"{_KEY_R2A}{new_refresh_value}",
            new_access_value,
            ex=at_ttl or _FALLBACK_TTL,
        )
        await pipe.execute()

        return OAuthToken(
            access_token=new_access_value,
            token_type="Bearer",  # noqa: S106
            expires_in=DEFAULT_ACCESS_TOKEN_EXPIRY,
            refresh_token=new_refresh_value,
            scope=" ".join(scopes) or None,
        )

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    async def _revoke_internal(
        self,
        access_token_str: str | None = None,
        refresh_token_str: str | None = None,
    ) -> None:
        keys_to_delete: list[str] = []

        if access_token_str:
            keys_to_delete.append(f"{_KEY_ACCESS_TOKEN}{access_token_str}")
            keys_to_delete.append(f"{_KEY_A2R}{access_token_str}")
            assoc_refresh = await self._redis_get(f"{_KEY_A2R}{access_token_str}")
            if assoc_refresh:
                keys_to_delete.append(f"{_KEY_REFRESH_TOKEN}{assoc_refresh}")
                keys_to_delete.append(f"{_KEY_R2A}{assoc_refresh}")

        if refresh_token_str:
            keys_to_delete.append(f"{_KEY_REFRESH_TOKEN}{refresh_token_str}")
            keys_to_delete.append(f"{_KEY_R2A}{refresh_token_str}")
            assoc_access = await self._redis_get(f"{_KEY_R2A}{refresh_token_str}")
            if assoc_access:
                keys_to_delete.append(f"{_KEY_ACCESS_TOKEN}{assoc_access}")
                keys_to_delete.append(f"{_KEY_A2R}{assoc_access}")

        if keys_to_delete:
            await self._redis_del(*keys_to_delete)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            await self._revoke_internal(access_token_str=token.token)
        elif isinstance(token, RefreshToken):
            await self._revoke_internal(refresh_token_str=token.token)
