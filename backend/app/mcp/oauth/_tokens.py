"""Token exchange, load, refresh, and revocation mixin."""
from __future__ import annotations

import logging
import secrets
import time

from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.provider import AuthorizationCode, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ._redis import (
    _FALLBACK_TTL,
    _KEY_A2R,
    _KEY_ACCESS_TOKEN,
    _KEY_AUTH_CODE,
    _KEY_R2A,
    _KEY_REFRESH_TOKEN,
    DEFAULT_ACCESS_TOKEN_EXPIRY,
    DEFAULT_REFRESH_TOKEN_EXPIRY,
)

logger = logging.getLogger(__name__)


class TokenMixin:
    """exchange_authorization_code / load_access_token / load_refresh_token /
    exchange_refresh_token / revoke_token.

    Mixin only: relies on base provider for redis helpers and TTL math.
    """

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        logger.info("MCP OAuth: token exchange for client=%s", client.client_id)
        # getdel でアトミックに取得+削除（TOCTOU 防止）
        r = self._get_redis()  # type: ignore[attr-defined]
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

        at_ttl = self._ttl_from_expires(access_expires_at)  # type: ignore[attr-defined]
        rt_ttl = self._ttl_from_expires(refresh_expires_at)  # type: ignore[attr-defined]

        r = self._get_redis()  # type: ignore[attr-defined]
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

    async def load_access_token(self, token: str) -> AccessToken | None:
        raw = await self._redis_get(f"{_KEY_ACCESS_TOKEN}{token}")  # type: ignore[attr-defined]
        if raw is None:
            return None

        token_obj = AccessToken.model_validate_json(raw)
        if token_obj.expires_at is not None and token_obj.expires_at < time.time():
            await self._revoke_internal(access_token_str=token)
            return None
        return token_obj

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        raw = await self._redis_get(f"{_KEY_REFRESH_TOKEN}{refresh_token}")  # type: ignore[attr-defined]
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
        old_access_str = await self._redis_get(f"{_KEY_R2A}{refresh_token.token}")  # type: ignore[attr-defined]
        if old_access_str:
            old_access_raw = await self._redis_get(  # type: ignore[attr-defined]
                f"{_KEY_ACCESS_TOKEN}{old_access_str}"
            )
            if old_access_raw:
                old_access = AccessToken.model_validate_json(old_access_raw)
                if hasattr(old_access, "claims") and old_access.claims:
                    old_user_id = old_access.claims.get("user_id")

        await self._revoke_internal(refresh_token_str=refresh_token.token)

        # ユーザーの有効性を再検証
        if old_user_id:
            from ...models import User

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

        at_ttl = self._ttl_from_expires(access_expires_at)  # type: ignore[attr-defined]
        rt_ttl = self._ttl_from_expires(refresh_expires_at)  # type: ignore[attr-defined]

        r = self._get_redis()  # type: ignore[attr-defined]
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

    async def _revoke_internal(
        self,
        access_token_str: str | None = None,
        refresh_token_str: str | None = None,
    ) -> None:
        keys_to_delete: list[str] = []

        if access_token_str:
            keys_to_delete.append(f"{_KEY_ACCESS_TOKEN}{access_token_str}")
            keys_to_delete.append(f"{_KEY_A2R}{access_token_str}")
            assoc_refresh = await self._redis_get(f"{_KEY_A2R}{access_token_str}")  # type: ignore[attr-defined]
            if assoc_refresh:
                keys_to_delete.append(f"{_KEY_REFRESH_TOKEN}{assoc_refresh}")
                keys_to_delete.append(f"{_KEY_R2A}{assoc_refresh}")

        if refresh_token_str:
            keys_to_delete.append(f"{_KEY_REFRESH_TOKEN}{refresh_token_str}")
            keys_to_delete.append(f"{_KEY_R2A}{refresh_token_str}")
            assoc_access = await self._redis_get(f"{_KEY_R2A}{refresh_token_str}")  # type: ignore[attr-defined]
            if assoc_access:
                keys_to_delete.append(f"{_KEY_ACCESS_TOKEN}{assoc_access}")
                keys_to_delete.append(f"{_KEY_A2R}{assoc_access}")

        if keys_to_delete:
            await self._redis_del(*keys_to_delete)  # type: ignore[attr-defined]

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            await self._revoke_internal(access_token_str=token.token)
        elif isinstance(token, RefreshToken):
            await self._revoke_internal(refresh_token_str=token.token)
