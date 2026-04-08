"""Redis singleton + key-space constants for the MCP OAuth provider.

A single connection pool is shared across all mixins. Tests monkeypatch
``app.mcp.oauth._redis._mcp_redis`` directly to inject fakeredis.
"""
from __future__ import annotations

import redis.asyncio as aioredis

# ── Singleton ────────────────────────────────────────────────

_mcp_redis: aioredis.Redis | None = None


def get_mcp_redis() -> aioredis.Redis:
    """MCP 用 Redis クライアントを返す（シングルトン）。"""
    global _mcp_redis
    if _mcp_redis is None:
        from ...core.config import settings
        _mcp_redis = aioredis.from_url(settings.REDIS_MCP_URI, decode_responses=True)
    return _mcp_redis


async def close_mcp_redis() -> None:
    """シャットダウン時に呼ぶ。"""
    global _mcp_redis
    if _mcp_redis is not None:
        await _mcp_redis.aclose()
        _mcp_redis = None


# ── Pending-auth consent flow (shared with oauth_consent.py) ─

PENDING_AUTH_PREFIX = "todo:mcp:pending_auth:"
PENDING_AUTH_TTL = 600  # 10 分


# ── Redis key prefixes (provider-private) ────────────────────

_KEY_CLIENT = "todo:mcp:client:"
_KEY_AUTH_CODE = "todo:mcp:auth_code:"
_KEY_ACCESS_TOKEN = "todo:mcp:access_token:"  # noqa: S105
_KEY_REFRESH_TOKEN = "todo:mcp:refresh_token:"  # noqa: S105
_KEY_A2R = "todo:mcp:a2r:"  # access_token -> refresh_token
_KEY_R2A = "todo:mcp:r2a:"  # refresh_token -> access_token


# ── Default expirations (seconds) ────────────────────────────

DEFAULT_AUTH_CODE_EXPIRY = 5 * 60  # 5 分
DEFAULT_ACCESS_TOKEN_EXPIRY = 7 * 24 * 60 * 60  # 7 日
DEFAULT_REFRESH_TOKEN_EXPIRY: int | None = None  # 無期限
# 無期限トークンの Redis TTL（30 日）
_FALLBACK_TTL = 30 * 24 * 60 * 60
