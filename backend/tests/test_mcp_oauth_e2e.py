"""MCP OAuth 2.1 E2E テスト

OAuth フロー全体を検証する:
  register → authorize → consent → token exchange → Bearer MCP リクエスト

X-API-Key 認証との共存も検証する。
"""

import hashlib
import json
import re
import secrets
from base64 import urlsafe_b64encode
from urllib.parse import parse_qs, urlencode, urlparse

import fakeredis.aioredis as _fakeredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.mcp.oauth_provider import TodoOAuthProvider, get_mcp_redis
from app.models.user import AuthType, User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def admin_user():
    user = User(
        email="oauth-admin@test.com",
        name="OAuth Admin",
        auth_type=AuthType.admin,
        password_hash="dummy",
        is_admin=True,
        is_active=True,
    )
    await user.insert()
    return user


@pytest.fixture
def admin_jwt(admin_user):
    return create_access_token(str(admin_user.id))


@pytest_asyncio.fixture
async def oauth_app():
    """OAuth + consent router + MCP を含むテスト用 ASGI アプリ"""
    import fastmcp.server.http as _fmcp_http
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from app.mcp.oauth_consent import router as consent_router
    from app.mcp.server import MCP_PATH, MOUNT_PREFIX, mcp as _mcp_server
    from app.mcp.server import _oauth_provider
    from app.mcp.well_known import McpTrailingSlashMiddleware

    # MCP server にツールを登録
    from app.mcp.server import register_tools
    register_tools()

    # MCP subapp（auth 付き）
    mcp_app = _fmcp_http.create_streamable_http_app(
        server=_mcp_server,
        streamable_http_path=MCP_PATH,
        auth=_mcp_server.auth,
    )

    # FastAPI app（consent router 用）
    from fastapi import FastAPI
    app = FastAPI()
    app.add_middleware(McpTrailingSlashMiddleware)

    # well-known routes（FastMCP 自動生成）
    for route in _oauth_provider.get_well_known_routes(mcp_path=MCP_PATH):
        app.routes.insert(0, route)

    # consent router
    app.include_router(consent_router)

    # MCP mount
    app.mount(MOUNT_PREFIX, mcp_app)

    yield app


@pytest_asyncio.fixture
async def oauth_client(oauth_app):
    async with AsyncClient(
        transport=ASGITransport(app=oauth_app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def _patch_mcp_redis():
    """OAuth プロバイダの Redis をテスト用 fakeredis に差し替え"""
    import app.mcp.oauth_provider as mod
    fake = _fakeredis.FakeRedis(decode_responses=True)
    mod._mcp_redis = fake
    yield
    await fake.aclose()
    mod._mcp_redis = None


def _pkce_pair():
    """PKCE code_verifier と code_challenge (S256) のペアを生成"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOAuthE2EFlow:
    """OAuth 2.1 フロー全体の E2E テスト"""

    async def test_full_oauth_flow(self, oauth_client: AsyncClient, admin_user, admin_jwt):
        """register → authorize → consent → token → Bearer MCP request"""
        client = oauth_client
        verifier, challenge = _pkce_pair()

        # ── Step 1: well-known discovery ──
        res = await client.get("/.well-known/oauth-authorization-server")
        assert res.status_code == 200
        metadata = res.json()
        assert "authorization_endpoint" in metadata
        assert "token_endpoint" in metadata
        assert "registration_endpoint" in metadata

        res2 = await client.get("/.well-known/oauth-protected-resource")
        assert res2.status_code == 200
        prm = res2.json()
        assert "authorization_servers" in prm

        # ── Step 2: Dynamic Client Registration (POST) ──
        reg_res = await client.post(
            "/mcp/register",
            json={
                "redirect_uris": ["http://localhost:9999/callback"],
                "client_name": "E2E Test Client",
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        assert reg_res.status_code == 201, reg_res.text
        client_id = reg_res.json()["client_id"]
        assert client_id

        # ── Step 3: Authorization request ──
        auth_params = urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:9999/callback",
            "state": "test_state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        auth_res = await client.get(
            f"/mcp/authorize?{auth_params}",
            follow_redirects=False,
        )
        assert auth_res.status_code == 302
        consent_url = auth_res.headers["location"]
        assert "/api/v1/mcp/oauth/consent" in consent_url

        # ── Step 4: Consent page (with JWT cookie) ──
        consent_res = await client.get(
            consent_url,
            cookies={"access_token": admin_jwt},
            follow_redirects=False,
        )
        assert consent_res.status_code == 200
        html_body = consent_res.text
        assert "MCP Todo" in html_body
        assert "許可する" in html_body

        # Extract hidden fields
        pending_match = re.search(r'name="pending" value="([^"]+)"', html_body)
        token_match = re.search(r'name="consent_token" value="([^"]+)"', html_body)
        assert pending_match and token_match
        pending = pending_match.group(1)
        consent_token = token_match.group(1)

        # ── Step 5: Submit consent (allow) ──
        submit_res = await client.post(
            "/api/v1/mcp/oauth/consent",
            data={
                "pending": pending,
                "consent_token": consent_token,
                "action": "allow",
            },
            cookies={"access_token": admin_jwt},
            follow_redirects=False,
        )
        assert submit_res.status_code == 302
        callback_url = submit_res.headers["location"]
        parsed = urlparse(callback_url)
        qs = parse_qs(parsed.query)
        auth_code = qs["code"][0]
        assert auth_code.startswith("todo_auth_")
        assert qs["state"][0] == "test_state"

        # ── Step 6: Token exchange ──
        token_res = await client.post(
            "/mcp/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://localhost:9999/callback",
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert token_res.status_code == 200, token_res.text
        token_data = token_res.json()
        assert token_data["token_type"].lower() == "bearer"
        assert token_data["access_token"].startswith("todo_at_")
        assert token_data["refresh_token"].startswith("todo_rt_")
        assert token_data["expires_in"] > 0
        access_token = token_data["access_token"]

        # ── Step 7: Bearer トークンが OAuthProvider で検証可能か確認 ──
        # lifespan 未実行のため MCP セッションマネージャが使えないので
        # OAuthProvider.load_access_token() で直接トークンの有効性を検証
        from app.mcp.server import _oauth_provider
        loaded = await _oauth_provider.load_access_token(access_token)
        assert loaded is not None, "access_token should be loadable"
        assert loaded.claims.get("user_id") == str(admin_user.id)

    async def test_consent_without_login_redirects(self, oauth_client: AsyncClient):
        """未ログインで同意画面にアクセスすると /login にリダイレクト"""
        # pending を Redis に直接作成
        r = get_mcp_redis()
        await r.set("todo:mcp:pending_auth:test123", json.dumps({
            "client_id": "dummy",
            "redirect_uri": "http://localhost/cb",
            "state": "s",
            "scopes": [],
            "code_challenge": "x",
        }), ex=600)

        res = await oauth_client.get(
            "/api/v1/mcp/oauth/consent?pending=test123",
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert res.headers["location"].startswith("/login?returnTo=")

    async def test_consent_expired_pending(self, oauth_client: AsyncClient, admin_jwt):
        """期限切れの pending で同意画面にアクセスするとエラー"""
        res = await oauth_client.get(
            "/api/v1/mcp/oauth/consent?pending=nonexistent",
            cookies={"access_token": admin_jwt},
        )
        assert res.status_code == 400
        assert "期限切れ" in res.text

    async def test_consent_deny(self, oauth_client: AsyncClient, admin_user, admin_jwt):
        """ユーザーが拒否すると access_denied エラーがコールバックに返る"""
        r = get_mcp_redis()
        pending_id = "deny_test"
        await r.set(f"todo:mcp:pending_auth:{pending_id}", json.dumps({
            "client_id": "test-client",
            "redirect_uri": "http://localhost:9999/callback",
            "state": "deny_state",
            "scopes": [],
            "code_challenge": "x",
        }), ex=600)

        # consent_token 生成
        consent_token = secrets.token_urlsafe(32)
        await r.set(f"todo:mcp:consent_token:{consent_token}", pending_id, ex=600)

        res = await oauth_client.post(
            "/api/v1/mcp/oauth/consent",
            data={
                "pending": pending_id,
                "consent_token": consent_token,
                "action": "deny",
            },
            cookies={"access_token": admin_jwt},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "access_denied" in res.headers["location"]

    async def test_double_submit_blocked(self, oauth_client: AsyncClient, admin_user, admin_jwt):
        """同じ consent_token での二重送信はブロックされる"""
        r = get_mcp_redis()
        pending_id = "double_test"
        await r.set(f"todo:mcp:pending_auth:{pending_id}", json.dumps({
            "client_id": "test-client",
            "redirect_uri": "http://localhost:9999/callback",
            "state": "s",
            "scopes": [],
            "code_challenge": "x",
        }), ex=600)

        consent_token = secrets.token_urlsafe(32)
        await r.set(f"todo:mcp:consent_token:{consent_token}", pending_id, ex=600)

        # 1回目: 成功
        res1 = await oauth_client.post(
            "/api/v1/mcp/oauth/consent",
            data={"pending": pending_id, "consent_token": consent_token, "action": "allow"},
            cookies={"access_token": admin_jwt},
            follow_redirects=False,
        )
        assert res1.status_code == 302

        # 2回目: 失敗（consent_token 消費済み）
        res2 = await oauth_client.post(
            "/api/v1/mcp/oauth/consent",
            data={"pending": pending_id, "consent_token": consent_token, "action": "allow"},
            cookies={"access_token": admin_jwt},
        )
        assert res2.status_code == 400

    async def test_auth_code_single_use(self, oauth_client: AsyncClient, admin_user, admin_jwt):
        """認可コードは1回のみ使用可能"""
        client = oauth_client
        verifier, challenge = _pkce_pair()

        # Register
        reg = await client.post("/mcp/register", json={
            "redirect_uris": ["http://localhost:9999/callback"],
            "client_name": "SingleUse",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        })
        client_id = reg.json()["client_id"]

        # Authorize
        auth_res = await client.get(
            f"/mcp/authorize?" + urlencode({
                "response_type": "code", "client_id": client_id,
                "redirect_uri": "http://localhost:9999/callback",
                "state": "s", "code_challenge": challenge, "code_challenge_method": "S256",
            }),
            follow_redirects=False,
        )
        consent_url = auth_res.headers["location"]

        # Consent
        consent_res = await client.get(consent_url, cookies={"access_token": admin_jwt})
        pending = re.search(r'name="pending" value="([^"]+)"', consent_res.text).group(1)
        ct = re.search(r'name="consent_token" value="([^"]+)"', consent_res.text).group(1)

        submit_res = await client.post(
            "/api/v1/mcp/oauth/consent",
            data={"pending": pending, "consent_token": ct, "action": "allow"},
            cookies={"access_token": admin_jwt},
            follow_redirects=False,
        )
        assert submit_res.status_code == 302
        callback_url = submit_res.headers["location"]
        auth_code = parse_qs(urlparse(callback_url).query)["code"][0]

        # Token exchange 1回目: 成功
        tok1 = await client.post("/mcp/token", data={
            "grant_type": "authorization_code", "code": auth_code,
            "redirect_uri": "http://localhost:9999/callback",
            "client_id": client_id, "code_verifier": verifier,
        })
        assert tok1.status_code == 200

        # Token exchange 2回目: 失敗（認可コード消費済み）
        tok2 = await client.post("/mcp/token", data={
            "grant_type": "authorization_code", "code": auth_code,
            "redirect_uri": "http://localhost:9999/callback",
            "client_id": client_id, "code_verifier": verifier,
        })
        assert tok2.status_code in (400, 401)  # FastMCP may return either


class TestXApiKeyCoexistence:
    """X-API-Key と OAuth の共存テスト"""

    async def test_xapikey_passes_auth_middleware(self, oauth_client: AsyncClient):
        """X-API-Key ヘッダーがあれば auth middleware を通過する（401 にならない）"""
        # lifespan 未実行のため MCP セッションマネージャが RuntimeError を出す。
        # この RuntimeError は auth middleware を通過した証拠（401 なら先に返る）。
        with pytest.raises(RuntimeError, match="task group"):
            await oauth_client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "apikey-test", "version": "1.0"},
                    },
                    "id": 1,
                },
                headers={
                    "Accept": "application/json, text/event-stream",
                    "X-API-Key": "any_key_value",
                },
            )

    async def test_no_auth_returns_401(self, oauth_client: AsyncClient):
        """認証なしリクエストは 401"""
        res = await oauth_client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "noauth", "version": "1.0"},
                },
                "id": 1,
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert res.status_code == 401

    async def test_empty_apikey_returns_401(self, oauth_client: AsyncClient):
        """空文字の X-API-Key は通過しない"""
        res = await oauth_client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "empty", "version": "1.0"},
                },
                "id": 1,
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "X-API-Key": "   ",
            },
        )
        assert res.status_code == 401

    async def test_register_get_returns_405(self, oauth_client: AsyncClient):
        """GET /mcp/register は 405"""
        res = await oauth_client.get("/mcp/register")
        assert res.status_code == 405
