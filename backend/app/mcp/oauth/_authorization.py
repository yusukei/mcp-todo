"""Authorization endpoint + auth-code storage mixin.

``authorize`` kicks off the consent flow (pending auth stored in Redis,
user redirected to ``/api/v1/mcp/oauth/consent``). Once the user approves,
``oauth_consent.py`` writes a ``TodoAuthorizationCode`` via
``store_authorization_code``, and the token endpoint later loads it via
``load_authorization_code``.
"""
from __future__ import annotations

import json
import logging
import secrets
import time

from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from ._models import TodoAuthorizationCode
from ._redis import _KEY_AUTH_CODE, PENDING_AUTH_PREFIX, PENDING_AUTH_TTL

logger = logging.getLogger(__name__)


class AuthorizationMixin:
    """authorize / store_authorization_code / load_authorization_code.

    Mixin only: relies on the base provider for redis helpers and
    ``self.base_url`` / ``self.get_client`` from its sibling mixins.
    """

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        logger.info("MCP OAuth: authorize request from client=%s", client.client_id)
        stored = await self.get_client(client.client_id or "")  # type: ignore[attr-defined]
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

        r = self._get_redis()  # type: ignore[attr-defined]
        await r.set(
            f"{PENDING_AUTH_PREFIX}{pending_id}",
            json.dumps(pending_data),
            ex=PENDING_AUTH_TTL,
        )

        # base_url は "https://host/mcp" 形式。ホスト部分を抽出して同意画面 URL を構築
        base_str = str(self.base_url).rstrip("/")  # type: ignore[attr-defined]
        # MOUNT_PREFIX ("/mcp") を除去してホスト URL を得る
        from ..server import MOUNT_PREFIX
        if base_str.endswith(MOUNT_PREFIX):
            host_url = base_str[: -len(MOUNT_PREFIX)]
        else:
            host_url = base_str
        return f"{host_url}/api/v1/mcp/oauth/consent?pending={pending_id}"

    async def store_authorization_code(self, code: AuthorizationCode) -> None:
        ttl = self._ttl_from_expires(code.expires_at)  # type: ignore[attr-defined]
        data = code.model_dump_json()
        if isinstance(code, TodoAuthorizationCode):
            raw = json.loads(data)
            raw["_type"] = "todo"
            data = json.dumps(raw)
        await self._redis_set(f"{_KEY_AUTH_CODE}{code.code}", data, ttl)  # type: ignore[attr-defined]

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = await self._redis_get(f"{_KEY_AUTH_CODE}{authorization_code}")  # type: ignore[attr-defined]
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
            await self._redis_del(f"{_KEY_AUTH_CODE}{authorization_code}")  # type: ignore[attr-defined]
            return None
        return code_obj
