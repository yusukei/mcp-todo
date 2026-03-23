"""認証エンドポイントの統合テスト"""

import pytest

from app.core.security import create_access_token, create_refresh_token
from app.core.redis import get_redis
from app.models import AllowedEmail, User
from app.models.user import AuthType
from app.core.security import hash_password


class TestLogin:
    async def test_login_success(self, client, admin_user):
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
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_email(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "nobody@test.com", "password": "pass"},
        )
        assert resp.status_code == 401

    async def test_login_inactive_user(self, client, inactive_user):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "inactive@test.com", "password": "pass"},
        )
        assert resp.status_code == 403

    async def test_login_google_user_cannot_use_password_auth(self, client, regular_user):
        """auth_type=google のユーザーはパスワード認証不可"""
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "user@test.com", "password": "anypass"},
        )
        assert resp.status_code == 401

    async def test_login_missing_body(self, client):
        resp = await client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    async def test_login_empty_password(self, client, admin_user):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": ""},
        )
        assert resp.status_code == 401


class TestRefresh:
    async def test_refresh_with_valid_token(self, client, admin_user):
        refresh, jti = create_refresh_token(str(admin_user.id))
        redis = get_redis()
        await redis.set(f"refresh_jti:{jti}", "valid", ex=604800)
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_with_access_token_fails(self, client, admin_user):
        """access トークンを refresh 用途に使えない"""
        access = create_access_token(str(admin_user.id))
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": access}
        )
        assert resp.status_code == 401

    async def test_refresh_with_tampered_token(self, client):
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "tampered.token.here"}
        )
        assert resp.status_code == 401

    async def test_refresh_user_not_found(self, client):
        """存在しないユーザーの refresh トークン"""
        token, jti = create_refresh_token("000000000000000000000000")
        redis = get_redis()
        await redis.set(f"refresh_jti:{jti}", "valid", ex=604800)
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": token}
        )
        assert resp.status_code == 401

    async def test_refresh_inactive_user(self, client, inactive_user):
        token, jti = create_refresh_token(str(inactive_user.id))
        redis = get_redis()
        await redis.set(f"refresh_jti:{jti}", "valid", ex=604800)
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": token}
        )
        assert resp.status_code == 401


class TestMe:
    async def test_me_with_valid_token(self, client, admin_user, admin_headers):
        resp = await client.get("/api/v1/auth/me", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "admin@test.com"
        assert data["is_admin"] is True
        assert "id" in data
        assert "name" in data

    async def test_me_without_token(self, client):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_me_with_refresh_token(self, client, admin_user):
        """refresh トークンは /me に使えない"""
        refresh, _ = create_refresh_token(str(admin_user.id))
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refresh}"},
        )
        assert resp.status_code == 401

    async def test_me_with_tampered_token(self, client):
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer tampered.token.value"},
        )
        assert resp.status_code == 401

    async def test_me_inactive_user(self, client, inactive_user):
        token = create_access_token(str(inactive_user.id))
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    async def test_me_returns_correct_regular_user(self, client, regular_user, user_headers):
        resp = await client.get("/api/v1/auth/me", headers=user_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "user@test.com"
        assert data["is_admin"] is False


class TestGoogleLogin:
    """Google OAuth ログイン開始 (/auth/google)"""

    async def test_google_login_stores_state_in_redis(self, client):
        """リダイレクト時に state を Redis に保存する"""
        from app.core.redis import get_redis

        resp = await client.get("/api/v1/auth/google", follow_redirects=False)
        assert resp.status_code == 307

        location = resp.headers["location"]
        assert "state=" in location

        # URL から state パラメータを抽出
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(location)
        state = parse_qs(parsed.query)["state"][0]

        # Redis に state が保存されていることを確認
        redis = get_redis()
        stored = await redis.get(f"oauth:state:{state}")
        assert stored == "1"


class TestGoogleCallbackBoundary:
    """Google OAuth コールバックの境界条件 (外部 HTTP 通信なし)"""

    async def test_callback_missing_code_param(self, client):
        resp = await client.get("/api/v1/auth/google/callback?state=abc")
        assert resp.status_code == 422

    async def test_callback_missing_state_param(self, client):
        resp = await client.get("/api/v1/auth/google/callback?code=abc")
        assert resp.status_code == 422

    async def test_callback_invalid_state(self, client):
        """Redis に存在しない state は 400"""
        resp = await client.get(
            "/api/v1/auth/google/callback?code=fake-code&state=nonexistent-state"
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid state"

    async def test_callback_state_consumed_after_use(self, client):
        """state は使用後に Redis から削除される (リプレイ攻撃防止)"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.core.redis import get_redis

        await AllowedEmail(email="consumed@example.com").insert()

        redis = get_redis()
        await redis.set("oauth:state:consume-test", "1", ex=600)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "email": "consumed@example.com",
            "name": "Consumed",
            "sub": "google-sub-consume",
        }
        mock_oauth = AsyncMock()
        mock_oauth.fetch_token = AsyncMock(return_value={"access_token": "fake"})
        mock_oauth.get = AsyncMock(return_value=mock_resp)
        mock_oauth.__aenter__ = AsyncMock(return_value=mock_oauth)
        mock_oauth.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.api.v1.endpoints.auth.AsyncOAuth2Client", return_value=mock_oauth
        ):
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake-code&state=consume-test"
            )
        assert resp.status_code == 200

        # state が Redis から削除されていることを確認
        assert await redis.get("oauth:state:consume-test") is None

        # 同じ state で再度リクエストすると 400
        resp = await client.get(
            "/api/v1/auth/google/callback?code=fake-code&state=consume-test"
        )
        assert resp.status_code == 400

    async def test_callback_email_not_in_allowed_list(self, client):
        """AllowedEmail に存在しないメールは 403"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.core.redis import get_redis

        redis = get_redis()
        await redis.set("oauth:state:test-state", "1", ex=600)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "email": "notallowed@example.com",
            "name": "Not Allowed",
            "sub": "google-sub-123",
        }
        mock_oauth = AsyncMock()
        mock_oauth.fetch_token = AsyncMock(return_value={"access_token": "fake"})
        mock_oauth.get = AsyncMock(return_value=mock_resp)
        mock_oauth.__aenter__ = AsyncMock(return_value=mock_oauth)
        mock_oauth.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.api.v1.endpoints.auth.AsyncOAuth2Client", return_value=mock_oauth
        ):
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake-code&state=test-state"
            )
        assert resp.status_code == 403

    async def test_callback_allowed_email_creates_user(self, client):
        """AllowedEmail にあるメールは新規ユーザーを作成してトークンを返す"""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.core.redis import get_redis

        await AllowedEmail(email="newuser@example.com").insert()

        redis = get_redis()
        await redis.set("oauth:state:test-state-2", "1", ex=600)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "email": "newuser@example.com",
            "name": "New User",
            "sub": "google-sub-456",
            "picture": "https://example.com/pic.jpg",
        }
        mock_oauth = AsyncMock()
        mock_oauth.fetch_token = AsyncMock(return_value={"access_token": "fake"})
        mock_oauth.get = AsyncMock(return_value=mock_resp)
        mock_oauth.__aenter__ = AsyncMock(return_value=mock_oauth)
        mock_oauth.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.api.v1.endpoints.auth.AsyncOAuth2Client", return_value=mock_oauth
        ):
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake-code&state=test-state-2"
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

        user = await User.find_one(User.email == "newuser@example.com")
        assert user is not None
        assert user.auth_type == AuthType.google
