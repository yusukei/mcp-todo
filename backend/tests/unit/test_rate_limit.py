"""Tests for login rate limiting (app/api/v1/endpoints/auth.py).

Covers _check_rate_limit, _record_failed_login, _clear_login_attempts
and their interaction as an integrated flow.
"""

import fakeredis.aioredis
import pytest
from fastapi import HTTPException
from unittest.mock import patch

from app.api.v1.endpoints.auth._shared import (
    _check_rate_limit,
    _clear_login_attempts,
    _record_failed_login,
    _LOGIN_LOCKOUT_SECONDS,
    _LOGIN_MAX_ATTEMPTS,
)


@pytest.fixture
def fake_redis():
    """Create an isolated fakeredis instance for rate limit tests."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _patch_redis(fake_redis):
    """Patch get_redis() in auth module to return isolated fakeredis."""
    with patch("app.api.v1.endpoints.auth._shared.get_redis", return_value=fake_redis):
        yield


# ---------------------------------------------------------------------------
# _check_rate_limit
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    """Verify that _check_rate_limit blocks only when threshold is reached."""

    async def test_no_attempts_allows_login(self):
        """No key in Redis means no prior failures -- login allowed."""
        await _check_rate_limit("new@example.com")  # should not raise

    async def test_below_limit_allows_login(self, fake_redis):
        """Fewer than MAX_ATTEMPTS failures still allow login."""
        await fake_redis.set("login_attempts:user@example.com", "4")
        await _check_rate_limit("user@example.com")  # should not raise

    async def test_at_limit_blocks_login(self, fake_redis):
        """Exactly MAX_ATTEMPTS failures triggers 429."""
        await fake_redis.set(
            "login_attempts:user@example.com",
            str(_LOGIN_MAX_ATTEMPTS),
        )
        with pytest.raises(HTTPException) as exc_info:
            await _check_rate_limit("user@example.com")
        assert exc_info.value.status_code == 429
        assert "Too many login attempts" in exc_info.value.detail

    async def test_above_limit_blocks_login(self, fake_redis):
        """More than MAX_ATTEMPTS failures still triggers 429."""
        await fake_redis.set(
            "login_attempts:user@example.com",
            str(_LOGIN_MAX_ATTEMPTS + 5),
        )
        with pytest.raises(HTTPException) as exc_info:
            await _check_rate_limit("user@example.com")
        assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# _record_failed_login
# ---------------------------------------------------------------------------


class TestRecordFailedLogin:
    """Verify that _record_failed_login increments counter and sets TTL."""

    async def test_first_failure_sets_count_to_1(self, fake_redis):
        """First failed login creates key with value 1."""
        email = "first@example.com"
        await _record_failed_login(email)

        value = await fake_redis.get(f"login_attempts:{email}")
        assert value == "1"

    async def test_increments_existing_count(self, fake_redis):
        """Subsequent failures increment the existing counter."""
        email = "repeat@example.com"
        await fake_redis.set(f"login_attempts:{email}", "3")

        await _record_failed_login(email)

        value = await fake_redis.get(f"login_attempts:{email}")
        assert value == "4"

    async def test_sets_ttl(self, fake_redis):
        """Key has a TTL equal to _LOGIN_LOCKOUT_SECONDS."""
        email = "ttl@example.com"
        await _record_failed_login(email)

        ttl = await fake_redis.ttl(f"login_attempts:{email}")
        # TTL should be positive and at most _LOGIN_LOCKOUT_SECONDS.
        # Allow 1 second of tolerance for execution time.
        assert 0 < ttl <= _LOGIN_LOCKOUT_SECONDS

    async def test_ttl_refreshed_on_subsequent_failure(self, fake_redis):
        """Each failure resets the TTL so the window extends."""
        email = "refresh@example.com"
        key = f"login_attempts:{email}"

        # First failure
        await _record_failed_login(email)

        # Artificially reduce TTL to simulate time passing
        await fake_redis.expire(key, 100)
        ttl_before = await fake_redis.ttl(key)
        assert ttl_before <= 100

        # Second failure -- TTL should be reset to full lockout
        await _record_failed_login(email)

        ttl_after = await fake_redis.ttl(key)
        assert ttl_after > ttl_before


# ---------------------------------------------------------------------------
# _clear_login_attempts
# ---------------------------------------------------------------------------


class TestClearLoginAttempts:
    """Verify that _clear_login_attempts removes the counter."""

    async def test_clears_existing_attempts(self, fake_redis):
        """Existing login_attempts key is deleted after clear."""
        email = "clear@example.com"
        await fake_redis.set(f"login_attempts:{email}", "3")

        await _clear_login_attempts(email)

        value = await fake_redis.get(f"login_attempts:{email}")
        assert value is None

    async def test_clear_nonexistent_is_noop(self):
        """Clearing attempts for email with no key does not raise."""
        await _clear_login_attempts("nobody@example.com")  # should not raise


# ---------------------------------------------------------------------------
# Integration: combined rate-limit flow
# ---------------------------------------------------------------------------


class TestRateLimitIntegration:
    """End-to-end scenarios exercising the full rate-limit lifecycle."""

    async def test_five_failures_then_blocked(self):
        """Recording MAX_ATTEMPTS failures causes _check_rate_limit to reject."""
        email = "blocked@example.com"
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            await _record_failed_login(email)

        with pytest.raises(HTTPException) as exc_info:
            await _check_rate_limit(email)
        assert exc_info.value.status_code == 429

    async def test_success_clears_after_failures(self):
        """Clearing attempts after failures re-allows login checks."""
        email = "recovered@example.com"

        # Accumulate failures up to the limit
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            await _record_failed_login(email)

        # Verify blocked
        with pytest.raises(HTTPException):
            await _check_rate_limit(email)

        # Successful login clears attempts
        await _clear_login_attempts(email)

        # Now the check passes again
        await _check_rate_limit(email)  # should not raise

    async def test_different_emails_independent(self):
        """Rate limits are tracked per email, not globally."""
        email_a = "alice@example.com"
        email_b = "bob@example.com"

        # Lock out alice
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            await _record_failed_login(email_a)

        # Alice is blocked
        with pytest.raises(HTTPException):
            await _check_rate_limit(email_a)

        # Bob is unaffected
        await _check_rate_limit(email_b)  # should not raise
