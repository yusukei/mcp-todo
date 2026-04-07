"""
認証エンドポイントのテスト

refresh token の JTI (JWT ID) によるワンタイム使用制御、
ログインレートリミット、トークン有効期限切れのテストを行う。
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest

from app.core.config import settings
from app.core.redis import get_redis
from app.api.v1.endpoints import auth as _auth_module
from app.core.security import ALGORITHM, create_refresh_token


class TestLogin:
    """POST /api/v1/auth/login のテスト"""

    async def test_login_success(self, client, admin_user):
        """正しい認証情報でトークンが返る"""
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, client, admin_user):
        """パスワードが間違っている場合は 401"""
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client):
        """存在しないユーザーは 401"""
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "nobody@test.com", "password": "pass"},
        )
        assert resp.status_code == 401

    async def test_login_inactive_user(self, client, inactive_user):
        """無効化されたユーザーは 403"""
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "inactive@test.com", "password": "pass"},
        )
        assert resp.status_code == 403

    async def test_login_rate_limit_after_max_attempts(self, client, admin_user):
        """5回連続で失敗するとレートリミット (429)"""
        for _ in range(_auth_module._LOGIN_MAX_ATTEMPTS):
            await client.post(
                "/api/v1/auth/login",
                json={"username": "admin@test.com", "password": "wrongpass"},
            )

        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "wrongpass"},
        )
        assert resp.status_code == 429
        assert "Too many login attempts" in resp.json()["detail"]

    async def test_login_rate_limit_blocks_correct_password_too(self, client, admin_user):
        """レートリミット中は正しいパスワードでも 429"""
        for _ in range(_auth_module._LOGIN_MAX_ATTEMPTS):
            await client.post(
                "/api/v1/auth/login",
                json={"username": "admin@test.com", "password": "wrongpass"},
            )

        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        assert resp.status_code == 429

    async def test_login_rate_limit_is_per_email(self, client, admin_user):
        """レートリミットはメールアドレスごとに独立"""
        for _ in range(_auth_module._LOGIN_MAX_ATTEMPTS):
            await client.post(
                "/api/v1/auth/login",
                json={"username": "admin@test.com", "password": "wrongpass"},
            )

        # Different email should not be rate-limited
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "other@test.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401  # Not 429

    async def test_successful_login_clears_rate_limit(self, client, admin_user):
        """ログイン成功で失敗カウンターがリセットされる"""
        # Accumulate 4 failures (just below the limit)
        for _ in range(_auth_module._LOGIN_MAX_ATTEMPTS - 1):
            await client.post(
                "/api/v1/auth/login",
                json={"username": "admin@test.com", "password": "wrongpass"},
            )

        # Successful login clears the counter
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        assert resp.status_code == 200

        # Now 5 more failures should be needed to trigger rate limit
        for _ in range(_auth_module._LOGIN_MAX_ATTEMPTS - 1):
            await client.post(
                "/api/v1/auth/login",
                json={"username": "admin@test.com", "password": "wrongpass"},
            )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        assert resp.status_code == 200  # Not 429


class TestRefreshToken:
    """POST /api/v1/auth/refresh のテスト"""

    async def test_refresh_token_happy_path(self, client, admin_user):
        """有効な refresh token で新しいトークンペアが返る"""
        # Login to get a valid refresh token
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # The new refresh token should be different (rotation)
        assert data["refresh_token"] != refresh_token

    async def test_refresh_token_reuse_is_rejected(self, client, admin_user):
        """使用済み refresh token の再利用は 401 (JTI ワンタイム使用)"""
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        # First use — should succeed
        resp1 = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp1.status_code == 200

        # Second use of the SAME token — should be rejected
        resp2 = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp2.status_code == 401
        assert "already used" in resp2.json()["detail"]

    async def test_refresh_token_rotation_chain(self, client, admin_user):
        """refresh token のローテーションチェーンが正しく動作する"""
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        current_token = login_resp.json()["refresh_token"]

        # Rotate 3 times; each new token should work exactly once
        for i in range(3):
            resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": current_token},
            )
            assert resp.status_code == 200, f"Rotation {i+1} failed"
            old_token = current_token
            current_token = resp.json()["refresh_token"]
            assert current_token != old_token

            # Old token should be invalid
            reuse_resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": old_token},
            )
            assert reuse_resp.status_code == 401

    async def test_refresh_token_expired(self, client, admin_user):
        """期限切れ refresh token は 401"""
        # Create an expired refresh token manually
        expired_payload = {
            "sub": str(admin_user.id),
            "exp": datetime.now(UTC) - timedelta(seconds=10),
            "type": "refresh",
            "jti": "expired-jti",
        }
        expired_token = jwt.encode(
            expired_payload,
            settings.REFRESH_SECRET_KEY,
            algorithm=ALGORITHM,
        )

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": expired_token},
        )
        assert resp.status_code == 401

    async def test_refresh_token_invalid_signature(self, client, admin_user):
        """不正な署名の refresh token は 401"""
        payload = {
            "sub": str(admin_user.id),
            "exp": datetime.now(UTC) + timedelta(days=7),
            "type": "refresh",
            "jti": "fake-jti",
        }
        bad_token = jwt.encode(payload, "wrong-secret-key", algorithm=ALGORITHM)

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": bad_token},
        )
        assert resp.status_code == 401

    async def test_refresh_token_wrong_type(self, client, admin_user):
        """type=access のトークンを refresh として使うと 401"""
        payload = {
            "sub": str(admin_user.id),
            "exp": datetime.now(UTC) + timedelta(days=7),
            "type": "access",  # wrong type
            "jti": "some-jti",
        }
        wrong_type_token = jwt.encode(
            payload,
            settings.REFRESH_SECRET_KEY,
            algorithm=ALGORITHM,
        )

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": wrong_type_token},
        )
        assert resp.status_code == 401

    async def test_refresh_token_with_forged_jti(self, client, admin_user):
        """Redis に登録されていない JTI のトークンは 401"""
        # Craft a valid-looking token with a JTI that was never stored in Redis
        payload = {
            "sub": str(admin_user.id),
            "exp": datetime.now(UTC) + timedelta(days=7),
            "type": "refresh",
            "jti": "forged-jti-never-stored",
        }
        forged_token = jwt.encode(
            payload,
            settings.REFRESH_SECRET_KEY,
            algorithm=ALGORITHM,
        )

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": forged_token},
        )
        assert resp.status_code == 401
        assert "already used" in resp.json()["detail"]

    async def test_refresh_token_for_inactive_user(self, client, admin_user):
        """ユーザーが無効化された後は refresh token が使えない"""
        # Login while user is active
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        # Deactivate the user
        admin_user.is_active = False
        await admin_user.save()

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 401
        assert "User not found" in resp.json()["detail"]

    async def test_refresh_token_backward_compat_no_jti(self, client, admin_user):
        """JTI なしの旧形式トークンは後方互換で受け入れられる"""
        # Create a token without JTI (simulating old tokens)
        payload = {
            "sub": str(admin_user.id),
            "exp": datetime.now(UTC) + timedelta(days=7),
            "type": "refresh",
            # No "jti" field
        }
        old_format_token = jwt.encode(
            payload,
            settings.REFRESH_SECRET_KEY,
            algorithm=ALGORITHM,
        )

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_format_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data


class TestRefreshRateLimitBypass:
    """refresh エンドポイントを悪用したレートリミットバイパスのテスト

    ログインレートリミットは email ベースだが、refresh エンドポイントは
    email を使わないため、レートリミットの対象外。ただし JTI によるワンタイム
    使用制御があるため、盗まれたトークンの再利用は防止される。
    """

    async def test_refresh_not_affected_by_login_rate_limit(self, client, admin_user):
        """login のレートリミットは refresh エンドポイントに影響しない"""
        # First get a valid refresh token
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        # Trigger login rate limit
        for _ in range(_auth_module._LOGIN_MAX_ATTEMPTS):
            await client.post(
                "/api/v1/auth/login",
                json={"username": "admin@test.com", "password": "wrongpass"},
            )

        # Login is now rate-limited
        login_blocked = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        assert login_blocked.status_code == 429

        # But refresh should still work (it doesn't use the login rate limiter)
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200

    async def test_refresh_cannot_be_used_to_generate_unlimited_tokens(self, client, admin_user):
        """refresh token のワンタイム使用により、無制限のトークン生成は不可能"""
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        stolen_token = login_resp.json()["refresh_token"]

        # Attacker tries to use the stolen token multiple times
        first_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": stolen_token},
        )
        assert first_resp.status_code == 200

        # All subsequent attempts with the same token fail
        for _ in range(_auth_module._LOGIN_MAX_ATTEMPTS):
            resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": stolen_token},
            )
            assert resp.status_code == 401
