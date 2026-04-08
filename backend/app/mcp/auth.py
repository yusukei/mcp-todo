"""MCP ツール用認証（デュアル認証）

OAuth 2.1 Bearer トークン（TodoOAuthProvider / Redis バックエンド）と
X-API-Key ヘッダーの両方をサポートする。

OAuth の場合: FastMCP ミドルウェアが Bearer トークンを検証済みの前提で、
ツール内ではトークンの claims["user_id"] からユーザーを解決する。

X-API-Key の場合: 従来の API キー認証をフォールバックとして維持する。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from ..core.config import settings
from ..core.security import hash_api_key
from ..models import McpApiKey, Project, User

logger = logging.getLogger(__name__)

# Auth cache: sha256(api_key) -> (result_dict, expiry_timestamp)
# TTL is operator-tunable via Settings; max-size is a code-level
# memory bound and stays a module constant.
AUTH_CACHE_MAX_SIZE = 1000


class _BoundedTTLCache(OrderedDict):
    """OrderedDict-based cache with TTL and max size (LRU eviction)."""

    def __init__(self, max_size: int = AUTH_CACHE_MAX_SIZE):
        super().__init__()
        self.max_size = max_size
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def get_valid(self, key: str) -> tuple[dict, float] | None:
        entry = self.get(key)
        if entry is None:
            return None
        result, expiry = entry
        if time.monotonic() >= expiry:
            del self[key]
            return None
        self.move_to_end(key)
        return entry

    def put(self, key: str, value: tuple[dict, float]) -> None:
        if key in self:
            self.move_to_end(key)
        self[key] = value
        while len(self) > self.max_size:
            self.popitem(last=False)

    async def aget_valid(self, key: str) -> tuple[dict, float] | None:
        async with self._get_lock():
            return self.get_valid(key)

    async def aput(self, key: str, value: tuple[dict, float]) -> None:
        async with self._get_lock():
            self.put(key, value)


_auth_cache = _BoundedTTLCache()


class McpAuthError(ToolError):
    pass


async def authenticate() -> dict:
    """MCP ツール呼び出し時の認証。

    1. OAuth Bearer トークン (FastMCP ミドルウェアが検証済み) -> ユーザー解決
    2. X-API-Key ヘッダー -> 従来の API キー認証（フォールバック）

    Returns:
        {"user_id": str, "user_name": str, "is_admin": bool, "auth_kind": str}
        ``auth_kind`` は ``"oauth"`` または ``"api_key"``。

        プロジェクトアクセス制御は ``Project.members`` を唯一のソースとして
        :func:`check_project_access` で都度判定する。API キー側に scopes 概念は
        持たず、API キーはオーナーユーザーの権限を継承する単なる credential。
    """
    # 1. HTTP リクエストを取得
    try:
        request = get_http_request()
    except RuntimeError:
        raise McpAuthError("HTTP request context unavailable") from None

    # 2. X-API-Key が存在すればそちらを優先（プレースホルダトークン経由のため）
    api_key = request.headers.get("x-api-key")
    if api_key:
        return await _resolve_api_key_user(api_key)

    # 3. OAuth Bearer トークンを確認
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except LookupError:
        token = None
    except Exception as e:
        logger.error("OAuth token retrieval failed: %s", e)
        raise McpAuthError("認証システムでエラーが発生しました") from e

    if token is not None:
        return await _resolve_oauth_user(token)

    raise McpAuthError("Authentication required")


async def _resolve_oauth_user(token: object) -> dict:
    """OAuth トークンの claims["user_id"] からユーザーを解決する。"""
    user_id = None
    if hasattr(token, "claims") and isinstance(token.claims, dict):  # type: ignore[union-attr]
        user_id = token.claims.get("user_id")  # type: ignore[union-attr]

    if not user_id:
        raise McpAuthError("OAuth トークンにユーザー情報がありません。再接続してください")

    user = await User.get(user_id)
    if not user or not user.is_active:
        raise McpAuthError("ユーザーが無効です")

    return {
        "user_id": str(user.id),
        "user_name": user.name,
        "is_admin": user.is_admin,
        "auth_kind": "oauth",
    }


async def _resolve_api_key_user(api_key: str) -> dict:
    """API キーからユーザーを解決する。"""
    cache_key = hash_api_key(api_key)

    cached = await _auth_cache.aget_valid(cache_key)
    if cached is not None:
        result, _expiry = cached
        return result

    api_key_doc = await McpApiKey.find_one(
        McpApiKey.key_hash == cache_key, McpApiKey.is_active == True  # noqa: E712
    )
    if not api_key_doc:
        raise McpAuthError("Invalid API key")

    if api_key_doc.created_by:
        owner = await User.get(api_key_doc.created_by.ref.id)
        if not owner or not owner.is_active:
            raise McpAuthError("API key owner is disabled")
    else:
        owner = None

    # Update last_used_at (throttled to once per 60s)
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    last_used = api_key_doc.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=UTC)
    if last_used is None or (now - last_used).total_seconds() > 60:
        api_key_doc.last_used_at = now
        await api_key_doc.save()

    if not owner:
        # Reject API keys without an owner: post-refactor the access decision
        # is anchored on the owner's Project membership, so an unowned key
        # has no defined access surface and must not authenticate.
        raise McpAuthError("API key has no owner user")

    result = {
        "key_id": str(api_key_doc.id),
        "key_name": api_key_doc.name,
        "user_id": str(owner.id),
        "user_name": owner.name,
        "is_admin": owner.is_admin,
        "auth_kind": "api_key",
    }
    await _auth_cache.aput(
        cache_key,
        (result, time.monotonic() + settings.MCP_AUTH_CACHE_TTL_SECONDS),
    )
    return result


async def check_project_access(project_id: str, key_info: dict) -> Project:
    """Verify the authenticated subject can access ``project_id``.

    Loads the project and checks membership. Admin users (``is_admin``) bypass
    the membership check. The loaded :class:`Project` is returned so callers
    can avoid an extra round-trip.

    Raises :class:`McpAuthError` if the project does not exist or the subject
    is not authorized.
    """
    project = await Project.get(project_id)
    if not project:
        raise McpAuthError(f"Project {project_id} not found")

    if key_info.get("is_admin"):
        return project

    user_id = key_info.get("user_id")
    if not user_id:
        raise McpAuthError("Authentication subject missing")

    if not project.has_member(user_id):
        raise McpAuthError(f"No access to project {project_id}")
    return project
