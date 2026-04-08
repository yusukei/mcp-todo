"""Base provider: OAuthProvider subclass that owns init, middleware, routes, and Redis helpers.

This class is the "left base" in the MRO chain — the pure mixins
(``ClientsMixin``, ``AuthorizationMixin``, ``TokenMixin``) resolve their
``self._redis_*`` helper calls here.
"""
from __future__ import annotations

import time

import redis.asyncio as aioredis
from fastmcp.server.auth.auth import OAuthProvider
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.routing import Route

from ._models import DualAuthBackend
from ._redis import _FALLBACK_TTL, get_mcp_redis


class _BaseProvider(OAuthProvider):
    """Concrete OAuthProvider subclass with init / middleware / routes / Redis helpers.

    The business-logic mixins (clients, authorization, tokens) inherit from
    plain ``object`` and reach these helpers via ``self.*`` at runtime.
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

    # ── Middleware ───────────────────────────────────────────

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

    # ── Discovery routes ─────────────────────────────────────

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

    # ── Redis helpers ────────────────────────────────────────

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
