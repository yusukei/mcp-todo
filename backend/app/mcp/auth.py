"""API key authentication for MCP tools.

Validates X-API-Key header directly against the database.
"""

import asyncio
import logging
import time
from collections import OrderedDict

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from ..core.security import hash_api_key
from ..models import McpApiKey, User

logger = logging.getLogger(__name__)

# Auth cache: sha256(api_key) -> (result_dict, expiry_timestamp)
# Bounded LRU+TTL cache to prevent unbounded memory growth from brute-force attempts.
AUTH_CACHE_TTL = 300  # 5 minutes
AUTH_CACHE_MAX_SIZE = 1000


class _BoundedTTLCache(OrderedDict):
    """OrderedDict-based cache with TTL and max size (LRU eviction).

    Provides both sync (get_valid/put) and async (aget_valid/aput) interfaces.
    The async methods use an internal asyncio.Lock for thread safety in
    concurrent async contexts.
    """

    def __init__(self, max_size: int = AUTH_CACHE_MAX_SIZE):
        super().__init__()
        self.max_size = max_size
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create the asyncio.Lock (must be called within a running loop)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def get_valid(self, key: str) -> tuple[dict, float] | None:
        """Return cached value if present and not expired, else None."""
        entry = self.get(key)
        if entry is None:
            return None
        result, expiry = entry
        if time.monotonic() >= expiry:
            del self[key]
            return None
        # Move to end (most recently used)
        self.move_to_end(key)
        return entry

    def put(self, key: str, value: tuple[dict, float]) -> None:
        """Insert/update entry, evicting oldest if at capacity."""
        if key in self:
            self.move_to_end(key)
        self[key] = value
        while len(self) > self.max_size:
            self.popitem(last=False)

    async def aget_valid(self, key: str) -> tuple[dict, float] | None:
        """Async-safe version of get_valid, protected by asyncio.Lock."""
        async with self._get_lock():
            return self.get_valid(key)

    async def aput(self, key: str, value: tuple[dict, float]) -> None:
        """Async-safe version of put, protected by asyncio.Lock."""
        async with self._get_lock():
            self.put(key, value)


_auth_cache = _BoundedTTLCache()


class McpAuthError(ToolError):
    pass


async def authenticate() -> dict:
    """Validate the X-API-Key header and return key info.

    Returns:
        {"key_id": str, "project_scopes": list[str]}
    """
    try:
        request = get_http_request()
    except RuntimeError:
        raise McpAuthError("HTTP request context unavailable")

    api_key = request.headers.get("x-api-key")
    if not api_key:
        raise McpAuthError("X-API-Key header required")

    cache_key = hash_api_key(api_key)

    # Check cache (async-safe)
    cached = await _auth_cache.aget_valid(cache_key)
    if cached is not None:
        result, _expiry = cached
        return result

    # Query DB directly
    api_key_doc = await McpApiKey.find_one(
        McpApiKey.key_hash == cache_key, McpApiKey.is_active == True  # noqa: E712
    )
    if not api_key_doc:
        raise McpAuthError("Invalid API key")

    # Check that the owning user is still active
    if api_key_doc.created_by:
        owner = await User.get(api_key_doc.created_by.ref.id)
        if not owner or not owner.is_active:
            raise McpAuthError("API key owner is disabled")

    # Update last_used_at (throttled to once per 60s)
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    last_used = api_key_doc.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=UTC)
    if last_used is None or (now - last_used).total_seconds() > 60:
        api_key_doc.last_used_at = now
        await api_key_doc.save()

    result = {
        "key_id": str(api_key_doc.id),
        "key_name": api_key_doc.name,
        "project_scopes": api_key_doc.project_scopes,
    }
    await _auth_cache.aput(cache_key, (result, time.monotonic() + AUTH_CACHE_TTL))
    return result


def check_project_access(project_id: str, scopes: list[str]) -> None:
    """Check project access. Empty scopes list means full access to all projects."""
    if scopes and project_id not in scopes:
        raise McpAuthError(f"No access to project {project_id}")
