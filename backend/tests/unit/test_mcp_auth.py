"""Tests for MCP authentication (app/mcp/auth.py)."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from app.core.security import hash_api_key
from app.mcp.auth import (
    AUTH_CACHE_MAX_SIZE,
    AUTH_CACHE_TTL,
    McpAuthError,
    _BoundedTTLCache,
    _auth_cache,
    authenticate,
    check_project_access,
)
from app.models import McpApiKey


@pytest.fixture(autouse=True)
def clear_auth_cache():
    """Clear auth cache before and after each test to avoid pollution."""
    _auth_cache.clear()
    yield
    _auth_cache.clear()


def _mock_request(api_key: str | None = None):
    """Create a mock HTTP request with optional X-API-Key header."""
    request = MagicMock()
    request.headers = MagicMock()
    request.headers.get = MagicMock(
        side_effect=lambda key: api_key if key == "x-api-key" else None
    )
    return request


RAW_KEY = "test-api-key-secret-value"


@pytest_asyncio.fixture
async def active_api_key(admin_user):
    """Create an active McpApiKey document in the mock DB."""
    doc = McpApiKey(
        key_hash=hash_api_key(RAW_KEY),
        name="Test Key",
        created_by=admin_user,
        is_active=True,
        last_used_at=None,
    )
    await doc.insert()
    return doc


@pytest_asyncio.fixture
async def inactive_api_key(admin_user):
    """Create an inactive McpApiKey document."""
    doc = McpApiKey(
        key_hash=hash_api_key("inactive-key-value"),
        name="Inactive Key",
        created_by=admin_user,
        is_active=False,
        last_used_at=None,
    )
    await doc.insert()
    return doc


class TestAuthenticate:
    """Tests for the authenticate() function."""

    @patch("app.mcp.auth.get_http_request")
    async def test_missing_api_key_header(self, mock_get_request):
        """No X-API-Key header raises McpAuthError."""
        mock_get_request.return_value = _mock_request(api_key=None)

        with pytest.raises(McpAuthError, match="Authentication required"):
            await authenticate()

    @patch("app.mcp.auth.get_http_request", side_effect=RuntimeError("no context"))
    async def test_http_context_unavailable(self, mock_get_request):
        """get_http_request raising RuntimeError raises McpAuthError."""
        with pytest.raises(McpAuthError, match="HTTP request context unavailable"):
            await authenticate()

    @patch("app.mcp.auth.get_http_request")
    async def test_invalid_api_key(self, mock_get_request):
        """Key not found in DB raises McpAuthError."""
        mock_get_request.return_value = _mock_request(api_key="nonexistent-key")

        with pytest.raises(McpAuthError, match="Invalid API key"):
            await authenticate()

    @patch("app.mcp.auth.get_http_request")
    async def test_inactive_api_key(self, mock_get_request, inactive_api_key):
        """Key exists but is_active=False raises McpAuthError."""
        mock_get_request.return_value = _mock_request(api_key="inactive-key-value")

        with pytest.raises(McpAuthError, match="Invalid API key"):
            await authenticate()

    @patch("app.mcp.auth.get_http_request")
    async def test_valid_api_key_returns_info(self, mock_get_request, active_api_key):
        """Valid active key returns dict with user identity and auth_kind."""
        mock_get_request.return_value = _mock_request(api_key=RAW_KEY)

        result = await authenticate()

        assert result["key_id"] == str(active_api_key.id)
        assert result["auth_kind"] == "api_key"
        assert result["user_id"] is not None
        assert result["is_admin"] is True
        # project_scopes is no longer part of the auth payload — access is
        # decided per-call by check_project_access against Project.members.
        assert "project_scopes" not in result

    @patch("app.mcp.auth.get_http_request")
    async def test_cache_hit_returns_cached(self, mock_get_request, active_api_key):
        """Second call within TTL returns cached result without DB query."""
        mock_get_request.return_value = _mock_request(api_key=RAW_KEY)

        # First call populates cache
        result1 = await authenticate()

        # Patch McpApiKey.find_one to verify it is NOT called on second request
        with patch.object(McpApiKey, "find_one") as mock_find:
            result2 = await authenticate()
            mock_find.assert_not_called()

        assert result1 == result2

    @patch("app.mcp.auth.get_http_request")
    async def test_cache_expired_queries_db(self, mock_get_request, active_api_key):
        """Expired cache entry triggers a fresh DB query."""
        mock_get_request.return_value = _mock_request(api_key=RAW_KEY)

        # First call populates cache
        await authenticate()

        # Manually expire the cache entry
        cache_key = hash_api_key(RAW_KEY)
        existing_result, _ = _auth_cache[cache_key]
        _auth_cache[cache_key] = (existing_result, time.monotonic() - 1)

        # Second call should query DB again because cache is expired
        with patch.object(
            McpApiKey, "find_one", wraps=McpApiKey.find_one
        ) as mock_find:
            result = await authenticate()
            mock_find.assert_called_once()

        assert result["key_id"] == str(active_api_key.id)

    @patch("app.mcp.auth.get_http_request")
    async def test_last_used_at_updated(self, mock_get_request, active_api_key):
        """First authentication updates last_used_at on the document."""
        mock_get_request.return_value = _mock_request(api_key=RAW_KEY)
        assert active_api_key.last_used_at is None

        await authenticate()

        # Reload from DB
        refreshed = await McpApiKey.get(active_api_key.id)
        assert refreshed.last_used_at is not None

    @patch("app.mcp.auth.get_http_request")
    async def test_last_used_at_throttled(self, mock_get_request, active_api_key):
        """Auth within 60 seconds does not update last_used_at again."""
        # Set last_used_at to 30 seconds ago (within 60s throttle window)
        recent_time = datetime.now(UTC) - timedelta(seconds=30)
        active_api_key.last_used_at = recent_time
        await active_api_key.save()

        mock_get_request.return_value = _mock_request(api_key=RAW_KEY)

        await authenticate()

        # Reload from DB - last_used_at should remain unchanged
        refreshed = await McpApiKey.get(active_api_key.id)
        # mongomock may strip tzinfo, so normalize both sides for comparison
        refreshed_ts = refreshed.last_used_at
        if refreshed_ts is not None and refreshed_ts.tzinfo is None:
            refreshed_ts = refreshed_ts.replace(tzinfo=UTC)
        expected_ts = recent_time
        if expected_ts.tzinfo is None:
            expected_ts = expected_ts.replace(tzinfo=UTC)
        delta = abs((refreshed_ts - expected_ts).total_seconds())
        assert delta < 1, "last_used_at should not have been updated within 60s window"

class TestCheckProjectAccess:
    """Tests for the check_project_access() function (post-refactor).

    The new contract: ``check_project_access`` loads the project from the DB
    and verifies the authenticated subject is either an admin or a member
    of ``project.members``. Empty scopes / global access patterns no longer
    exist.
    """

    async def test_admin_bypasses_membership(self, admin_user, test_project):
        """Admin users may access any project even if not a member."""
        key_info = {"user_id": str(admin_user.id), "is_admin": True, "auth_kind": "api_key"}
        result = await check_project_access(str(test_project.id), key_info)
        assert str(result.id) == str(test_project.id)

    async def test_member_allowed(self, regular_user, test_project):
        """Non-admin user that is a member of the project can access it."""
        from app.models.project import ProjectMember
        test_project.members.append(ProjectMember(user_id=str(regular_user.id)))
        await test_project.save()

        key_info = {"user_id": str(regular_user.id), "is_admin": False, "auth_kind": "oauth"}
        result = await check_project_access(str(test_project.id), key_info)
        assert str(result.id) == str(test_project.id)

    async def test_non_member_denied(self, regular_user, test_project):
        """Non-admin user that is not a member is denied."""
        # Ensure regular_user is NOT in members (test_project may have been
        # populated by fixtures)
        test_project.members = [
            m for m in test_project.members if m.user_id != str(regular_user.id)
        ]
        await test_project.save()

        key_info = {"user_id": str(regular_user.id), "is_admin": False, "auth_kind": "oauth"}
        with pytest.raises(McpAuthError, match="No access to project"):
            await check_project_access(str(test_project.id), key_info)

    async def test_missing_project_raises(self):
        """Unknown project_id raises McpAuthError."""
        from bson import ObjectId
        key_info = {"user_id": "any", "is_admin": True, "auth_kind": "api_key"}
        with pytest.raises(McpAuthError, match="not found"):
            await check_project_access(str(ObjectId()), key_info)

    async def test_missing_user_id_raises(self, test_project):
        """Non-admin key_info without user_id raises McpAuthError."""
        key_info = {"is_admin": False, "auth_kind": "oauth"}
        with pytest.raises(McpAuthError, match="Authentication subject missing"):
            await check_project_access(str(test_project.id), key_info)


class TestBoundedTTLCache:
    """Tests for the _BoundedTTLCache size limit."""

    def test_evicts_oldest_when_full(self):
        """Inserting beyond max_size evicts the oldest (LRU) entry."""
        cache = _BoundedTTLCache(max_size=3)
        future = time.monotonic() + 9999

        cache.put("a", ({"id": "a"}, future))
        cache.put("b", ({"id": "b"}, future))
        cache.put("c", ({"id": "c"}, future))
        assert len(cache) == 3

        # Adding a 4th entry should evict "a" (oldest)
        cache.put("d", ({"id": "d"}, future))
        assert len(cache) == 3
        assert cache.get_valid("a") is None
        assert cache.get_valid("d") is not None

    def test_access_refreshes_lru_order(self):
        """Accessing an entry moves it to end, so a different entry is evicted."""
        cache = _BoundedTTLCache(max_size=3)
        future = time.monotonic() + 9999

        cache.put("a", ({"id": "a"}, future))
        cache.put("b", ({"id": "b"}, future))
        cache.put("c", ({"id": "c"}, future))

        # Access "a" to refresh it (moves to end)
        cache.get_valid("a")

        # Adding "d" should now evict "b" (oldest after "a" was refreshed)
        cache.put("d", ({"id": "d"}, future))
        assert len(cache) == 3
        assert cache.get_valid("a") is not None
        assert cache.get_valid("b") is None

    def test_expired_entries_removed_on_access(self):
        """Accessing an expired entry removes it and returns None."""
        cache = _BoundedTTLCache(max_size=10)
        past = time.monotonic() - 1

        cache.put("expired", ({"id": "x"}, past))
        assert cache.get_valid("expired") is None
        assert len(cache) == 0

    def test_max_size_constant_is_1000(self):
        """AUTH_CACHE_MAX_SIZE should be 1000."""
        assert AUTH_CACHE_MAX_SIZE == 1000

    def test_global_cache_is_bounded(self):
        """The module-level _auth_cache is an instance of _BoundedTTLCache."""
        assert isinstance(_auth_cache, _BoundedTTLCache)
        assert _auth_cache.max_size == AUTH_CACHE_MAX_SIZE
