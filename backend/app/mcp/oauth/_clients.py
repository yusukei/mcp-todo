"""Dynamic Client Registration (RFC 7591) mixin."""
from __future__ import annotations

import logging

from mcp.shared.auth import OAuthClientInformationFull

from ._redis import _KEY_CLIENT

logger = logging.getLogger(__name__)


class ClientsMixin:
    """get_client / register_client — Redis-backed OAuth client storage.

    Mixin only: relies on ``self._redis_get`` / ``self._redis_set`` from
    the base provider and ``self.client_registration_options`` from
    the upstream ``OAuthProvider`` base class.
    """

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = await self._redis_get(f"{_KEY_CLIENT}{client_id}")  # type: ignore[attr-defined]
        if raw is None:
            return None
        return OAuthClientInformationFull.model_validate_json(raw)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        logger.info("MCP OAuth: client registration: %s", client_info.client_name)
        if (
            client_info.scope is not None
            and self.client_registration_options is not None  # type: ignore[attr-defined]
            and self.client_registration_options.valid_scopes is not None  # type: ignore[attr-defined]
        ):
            requested_scopes = set(client_info.scope.split())
            valid_scopes = set(self.client_registration_options.valid_scopes)  # type: ignore[attr-defined]
            invalid_scopes = requested_scopes - valid_scopes
            if invalid_scopes:
                raise ValueError(
                    f"Requested scopes are not valid: {', '.join(invalid_scopes)}"
                )

        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")

        await self._redis_set(  # type: ignore[attr-defined]
            f"{_KEY_CLIENT}{client_info.client_id}",
            client_info.model_dump_json(),
        )
