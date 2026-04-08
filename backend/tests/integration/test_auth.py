"""認証エンドポイントの統合テスト"""

from app.core.security import create_access_token, create_refresh_token
from app.core.redis import get_redis
from app.models import AllowedEmail, User
from app.models.user import AuthType


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
            "/api/v1/auth/refresh", cookies={"refresh_token": refresh}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_with_access_token_fails(self, client, admin_user):
        """access トークンを refresh 用途に使えない"""
        access = create_access_token(str(admin_user.id))
        resp = await client.post(
            "/api/v1/auth/refresh", cookies={"refresh_token": access}
        )
        assert resp.status_code == 401

    async def test_refresh_with_tampered_token(self, client):
        resp = await client.post(
            "/api/v1/auth/refresh", cookies={"refresh_token": "tampered.token.here"}
        )
        assert resp.status_code == 401

    async def test_refresh_without_cookie(self, client):
        """cookie が無い場合は 401 (body 経路は廃止)"""
        client.cookies.clear()
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401
        assert "Missing refresh token" in resp.json()["detail"]

    async def test_refresh_user_not_found(self, client):
        """存在しないユーザーの refresh トークン"""
        token, jti = create_refresh_token("000000000000000000000000")
        redis = get_redis()
        await redis.set(f"refresh_jti:{jti}", "valid", ex=604800)
        resp = await client.post(
            "/api/v1/auth/refresh", cookies={"refresh_token": token}
        )
        assert resp.status_code == 401

    async def test_refresh_inactive_user(self, client, inactive_user):
        token, jti = create_refresh_token(str(inactive_user.id))
        redis = get_redis()
        await redis.set(f"refresh_jti:{jti}", "valid", ex=604800)
        resp = await client.post(
            "/api/v1/auth/refresh", cookies={"refresh_token": token}
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


class TestMeViaCookie:
    """Cookie 経由の /auth/me 認証経路 (deps.get_current_user)"""

    async def test_me_with_access_token_cookie(self, client, admin_user):
        """access_token cookie だけで /auth/me が通る (Bearer 不要)"""
        token = create_access_token(str(admin_user.id))
        client.cookies.clear()
        resp = await client.get(
            "/api/v1/auth/me",
            cookies={"access_token": token},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "admin@test.com"

    async def test_me_cookie_takes_effect_when_no_bearer(self, client, admin_user):
        """Authorization ヘッダが無い場合に cookie へフォールバック"""
        token = create_access_token(str(admin_user.id))
        client.cookies.clear()
        resp = await client.get(
            "/api/v1/auth/me",
            cookies={"access_token": token},
        )
        assert resp.status_code == 200

    async def test_me_bearer_takes_precedence_over_cookie(self, client, admin_user):
        """Bearer ヘッダが cookie より優先される (旧 API クライアント互換)"""
        good = create_access_token(str(admin_user.id))
        client.cookies.clear()
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {good}"},
            cookies={"access_token": "garbage"},
        )
        assert resp.status_code == 200


class TestLoginCookieAttributes:
    """ログイン成功時の Set-Cookie 属性検証"""

    async def test_login_sets_httponly_cookies(self, client, admin_user):
        client.cookies.clear()
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        assert resp.status_code == 200

        set_cookie_headers = resp.headers.get_list("set-cookie")
        access_header = next((h for h in set_cookie_headers if h.startswith("access_token=")), None)
        refresh_header = next((h for h in set_cookie_headers if h.startswith("refresh_token=")), None)

        assert access_header is not None, "access_token cookie missing"
        assert refresh_header is not None, "refresh_token cookie missing"

        # access_token: HttpOnly, Path=/
        assert "HttpOnly" in access_header
        assert "Path=/" in access_header

        # refresh_token: HttpOnly, Path=/api/v1/auth (scoped)
        assert "HttpOnly" in refresh_header
        assert "Path=/api/v1/auth" in refresh_header

    async def test_login_failure_does_not_set_cookies(self, client, admin_user):
        client.cookies.clear()
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401
        set_cookie_headers = resp.headers.get_list("set-cookie")
        assert not any(h.startswith("access_token=") for h in set_cookie_headers)
        assert not any(h.startswith("refresh_token=") for h in set_cookie_headers)


class TestLogout:
    """POST /api/v1/auth/logout — 認証不要で cookie を消す"""

    async def test_logout_clears_cookies(self, client, admin_user):
        # First log in to populate cookies
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@test.com", "password": "adminpass"},
        )
        assert login_resp.status_code == 200

        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"detail": "Logged out"}

        set_cookie_headers = resp.headers.get_list("set-cookie")
        # Both cookies should be deleted (Max-Age=0 or expires in the past)
        access_clear = next((h for h in set_cookie_headers if h.startswith("access_token=")), None)
        refresh_clear = next((h for h in set_cookie_headers if h.startswith("refresh_token=")), None)
        assert access_clear is not None
        assert refresh_clear is not None
        assert "Max-Age=0" in access_clear or 'expires=Thu, 01 Jan 1970' in access_clear.lower()
        assert "Max-Age=0" in refresh_clear or 'expires=Thu, 01 Jan 1970' in refresh_clear.lower()

    async def test_logout_without_auth_still_succeeds(self, client):
        """access_token が無い (期限切れ) 状態でもログアウトは 200 を返す。

        以前は get_current_user を必須にしていたので、access_token が
        切れていると 401 を返し、フロントが logout を「成功した」と
        誤認する原因になっていた。"""
        client.cookies.clear()
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"detail": "Logged out"}


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
            "app.api.v1.endpoints.auth.google.AsyncOAuth2Client", return_value=mock_oauth
        ):
            client.cookies.clear()
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake-code&state=consume-test",
                cookies={"oauth_state": "consume-test"},
            )
        assert resp.status_code == 200

        # state が Redis から削除されていることを確認
        assert await redis.get("oauth:state:consume-test") is None

        # 同じ state で再度リクエストすると 400 (cookie 越しに同じ state を送っても Redis 側で消費済み)
        client.cookies.clear()
        resp = await client.get(
            "/api/v1/auth/google/callback?code=fake-code&state=consume-test",
            cookies={"oauth_state": "consume-test"},
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
            "app.api.v1.endpoints.auth.google.AsyncOAuth2Client", return_value=mock_oauth
        ):
            client.cookies.clear()
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake-code&state=test-state",
                cookies={"oauth_state": "test-state"},
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
            "app.api.v1.endpoints.auth.google.AsyncOAuth2Client", return_value=mock_oauth
        ):
            client.cookies.clear()
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake-code&state=test-state-2",
                cookies={"oauth_state": "test-state-2"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

        user = await User.find_one(User.email == "newuser@example.com")
        assert user is not None
        assert user.auth_type == AuthType.google


def _patched_oauth_response(email: str, name: str = "U", sub: str = "g-sub", picture: str | None = None):
    """Build an AsyncOAuth2Client mock that returns a fixed userinfo dict."""
    from unittest.mock import AsyncMock, MagicMock
    info: dict[str, str | None] = {"email": email, "name": name, "sub": sub}
    if picture is not None:
        info["picture"] = picture
    mock_resp = MagicMock()
    mock_resp.json.return_value = info
    mock_oauth = AsyncMock()
    mock_oauth.fetch_token = AsyncMock(return_value={"access_token": "fake"})
    mock_oauth.get = AsyncMock(return_value=mock_resp)
    mock_oauth.__aenter__ = AsyncMock(return_value=mock_oauth)
    mock_oauth.__aexit__ = AsyncMock(return_value=False)
    return mock_oauth


class TestGoogleCallbackStateBinding:
    """G1: state cookie の double-submit 検証

    攻撃者が自分の Google アカウントで OAuth を開始し、生成された
    state を被害者のブラウザに踏ませる「login CSRF」を防ぐため、
    /google/callback は ?state=... と oauth_state cookie の一致を要求する。
    """

    async def test_callback_rejects_when_cookie_missing(self, client):
        """cookie が無い場合は 400 (リダイレクトを直接踏んだケース)"""
        from app.core.redis import get_redis
        redis = get_redis()
        await redis.set("oauth:state:no-cookie", "1", ex=600)

        client.cookies.clear()
        resp = await client.get(
            "/api/v1/auth/google/callback?code=fake&state=no-cookie",
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid state"

    async def test_callback_rejects_when_cookie_mismatch(self, client):
        """cookie の値と URL の state が一致しない → 400 (login CSRF 防止)"""
        from app.core.redis import get_redis
        redis = get_redis()
        await redis.set("oauth:state:victim-state", "1", ex=600)

        client.cookies.clear()
        resp = await client.get(
            "/api/v1/auth/google/callback?code=fake&state=victim-state",
            cookies={"oauth_state": "attacker-state"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid state"

    async def test_login_redirect_sets_state_cookie(self, client):
        """/google エンドポイントは oauth_state cookie を Set する"""
        client.cookies.clear()
        resp = await client.get("/api/v1/auth/google", follow_redirects=False)
        assert resp.status_code == 307
        cookies_set = resp.headers.get_list("set-cookie")
        oauth_state_cookie = next(
            (c for c in cookies_set if c.startswith("oauth_state=")),
            None,
        )
        assert oauth_state_cookie is not None
        assert "HttpOnly" in oauth_state_cookie
        assert "Path=/api/v1/auth/google" in oauth_state_cookie


class TestGoogleCallbackUserBranches:
    """既存ユーザー更新 / 非active / DuplicateKeyError race のテスト"""

    async def test_callback_updates_existing_google_user(self, client):
        """既存 Google ユーザーは name/picture/google_id が更新される"""
        from unittest.mock import patch
        from app.core.redis import get_redis

        await AllowedEmail(email="existing@example.com").insert()
        existing = User(
            email="existing@example.com",
            name="Old Name",
            auth_type=AuthType.google,
            google_id="old-sub",
            picture_url="https://old.example.com/pic.jpg",
        )
        await existing.insert()

        redis = get_redis()
        await redis.set("oauth:state:upd-state", "1", ex=600)

        with patch(
            "app.api.v1.endpoints.auth.google.AsyncOAuth2Client",
            return_value=_patched_oauth_response(
                "existing@example.com",
                name="New Name",
                sub="new-sub",
                picture="https://new.example.com/pic.jpg",
            ),
        ):
            client.cookies.clear()
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake&state=upd-state",
                cookies={"oauth_state": "upd-state"},
            )

        assert resp.status_code == 200
        refreshed = await User.find_one(User.email == "existing@example.com")
        assert refreshed is not None
        assert refreshed.name == "New Name"
        assert refreshed.google_id == "new-sub"
        assert refreshed.picture_url == "https://new.example.com/pic.jpg"

    async def test_callback_inactive_user_returns_403(self, client):
        """is_active=False のユーザーは Google 経由でも 403"""
        from unittest.mock import patch
        from app.core.redis import get_redis

        await AllowedEmail(email="inactive-google@example.com").insert()
        u = User(
            email="inactive-google@example.com",
            name="Inactive",
            auth_type=AuthType.google,
            google_id="g-inactive",
            is_active=False,
        )
        await u.insert()

        redis = get_redis()
        await redis.set("oauth:state:inact-state", "1", ex=600)

        with patch(
            "app.api.v1.endpoints.auth.google.AsyncOAuth2Client",
            return_value=_patched_oauth_response("inactive-google@example.com"),
        ):
            client.cookies.clear()
            resp = await client.get(
                "/api/v1/auth/google/callback?code=fake&state=inact-state",
                cookies={"oauth_state": "inact-state"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Account disabled"

    async def test_callback_duplicate_key_race_recovers(self, client):
        """並行登録で DuplicateKeyError → 既存レコードを返す経路"""
        from unittest.mock import patch
        from pymongo.errors import DuplicateKeyError
        from app.core.redis import get_redis

        await AllowedEmail(email="race@example.com").insert()
        # 別のリクエストが既に作っていた状況をシミュレートするため、
        # User.insert を DuplicateKeyError でこかす一方、find_one は
        # 既存ユーザーを返すよう先に挿入しておく。
        pre_existing = User(
            email="race@example.com",
            name="Race Winner",
            auth_type=AuthType.google,
            google_id="race-sub",
        )
        await pre_existing.insert()

        redis = get_redis()
        await redis.set("oauth:state:race-state", "1", ex=600)

        # User.insert を DuplicateKeyError で上書き → race の起きた側
        original_insert = User.insert

        async def _raise_dup(self):  # type: ignore[no-untyped-def]
            raise DuplicateKeyError("simulated race")

        User.insert = _raise_dup  # type: ignore[method-assign]
        try:
            with patch(
                "app.api.v1.endpoints.auth.google.AsyncOAuth2Client",
                return_value=_patched_oauth_response("race@example.com"),
            ):
                client.cookies.clear()
                resp = await client.get(
                    "/api/v1/auth/google/callback?code=fake&state=race-state",
                    cookies={"oauth_state": "race-state"},
                )
        finally:
            User.insert = original_insert  # type: ignore[method-assign]

        assert resp.status_code == 200
