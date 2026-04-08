"""API キー管理エンドポイント (/api/v1/mcp-keys/*) の統合テスト

APIキーはユーザーごとに管理され、自分のキーのみ操作可能。
"""

import pytest
import pytest_asyncio

from app.core.security import hash_api_key
from app.models import McpApiKey


class TestCreateKey:
    async def test_user_can_create_key(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "My Key"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Key"
        assert "key" in data  # raw key は作成時のみ返される
        assert data["key"].startswith("mtodo_")
        assert "id" in data
        assert "created_at" in data

    async def test_regular_user_can_create_key(
        self, client, regular_user, user_headers
    ):
        resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "User Key"},
            headers=user_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "User Key"

    async def test_created_key_is_stored_hashed(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "Hash Test"},
            headers=admin_headers,
        )
        raw_key = resp.json()["key"]
        key_id = resp.json()["id"]

        db_key = await McpApiKey.get(key_id)
        assert db_key is not None
        assert db_key.key_hash == hash_api_key(raw_key)
        assert db_key.key_hash != raw_key  # ハッシュ化されている

    async def test_unauthenticated_cannot_create_key(self, client):
        resp = await client.post(
            "/api/v1/mcp-keys", json={"name": "No Auth"}
        )
        assert resp.status_code == 401


class TestListKeys:
    async def test_user_sees_only_own_keys(
        self, client, admin_user, regular_user, admin_headers, user_headers
    ):
        # admin creates a key
        await client.post(
            "/api/v1/mcp-keys",
            json={"name": "Admin Key"},
            headers=admin_headers,
        )
        # regular user creates a key
        await client.post(
            "/api/v1/mcp-keys",
            json={"name": "User Key"},
            headers=user_headers,
        )

        # admin sees only their own
        resp = await client.get("/api/v1/mcp-keys", headers=admin_headers)
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 1
        assert keys[0]["name"] == "Admin Key"

        # regular user sees only their own
        resp = await client.get("/api/v1/mcp-keys", headers=user_headers)
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 1
        assert keys[0]["name"] == "User Key"

    async def test_revoked_keys_not_listed(
        self, client, admin_user, admin_headers
    ):
        create_resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "To Revoke"},
            headers=admin_headers,
        )
        key_id = create_resp.json()["id"]

        await client.delete(f"/api/v1/mcp-keys/{key_id}", headers=admin_headers)

        resp = await client.get("/api/v1/mcp-keys", headers=admin_headers)
        ids = [k["id"] for k in resp.json()]
        assert key_id not in ids

    async def test_raw_key_not_in_list(
        self, client, admin_user, admin_headers
    ):
        await client.post(
            "/api/v1/mcp-keys",
            json={"name": "Key A"},
            headers=admin_headers,
        )
        resp = await client.get("/api/v1/mcp-keys", headers=admin_headers)
        for k in resp.json():
            assert "key" not in k

    async def test_unauthenticated_cannot_list_keys(self, client):
        resp = await client.get("/api/v1/mcp-keys")
        assert resp.status_code == 401


class TestRevokeKey:
    async def test_user_can_revoke_own_key(
        self, client, admin_user, admin_headers
    ):
        create_resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "Revoke Me"},
            headers=admin_headers,
        )
        key_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/mcp-keys/{key_id}", headers=admin_headers
        )
        assert resp.status_code == 204

        db_key = await McpApiKey.get(key_id)
        assert db_key is not None
        assert db_key.is_active is False

    async def test_cannot_revoke_other_users_key(
        self, client, admin_user, regular_user, admin_headers, user_headers
    ):
        # admin creates key
        create_resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "Admin Only"},
            headers=admin_headers,
        )
        key_id = create_resp.json()["id"]

        # regular user tries to revoke admin's key
        resp = await client.delete(
            f"/api/v1/mcp-keys/{key_id}", headers=user_headers
        )
        assert resp.status_code == 404

        # key is still active
        db_key = await McpApiKey.get(key_id)
        assert db_key.is_active is True

    async def test_revoke_nonexistent_key_returns_404(
        self, client, admin_user, admin_headers
    ):
        resp = await client.delete(
            "/api/v1/mcp-keys/000000000000000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_unauthenticated_cannot_revoke_key(self, client):
        resp = await client.delete("/api/v1/mcp-keys/000000000000000000000000")
        assert resp.status_code == 401
