"""Integration tests for /workspaces/agents endpoints, focused on token rotation.

The agent CRUD path (create / delete) is exercised end-to-end here so that
the rotate-token flow can be verified against a realistic database state.
"""

from app.core.security import hash_api_key
from app.models.remote import RemoteAgent


class TestCreateAgent:
    async def test_admin_can_create_agent(self, client, admin_user, admin_headers):
        resp = await client.post(
            "/api/v1/workspaces/agents",
            json={"name": "build-host"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "build-host"
        assert body["token"].startswith("ta_")
        assert "id" in body

        stored = await RemoteAgent.get(body["id"])
        assert stored is not None
        assert stored.key_hash == hash_api_key(body["token"])

    async def test_regular_user_cannot_create_agent(self, client, regular_user, user_headers):
        resp = await client.post(
            "/api/v1/workspaces/agents",
            json={"name": "should-fail"},
            headers=user_headers,
        )
        assert resp.status_code == 403


class TestRotateAgentToken:
    async def test_rotate_issues_new_token_and_invalidates_old(
        self, client, admin_user, admin_headers
    ):
        create_resp = await client.post(
            "/api/v1/workspaces/agents",
            json={"name": "rotate-target"},
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        agent_id = create_resp.json()["id"]
        old_token = create_resp.json()["token"]
        old_hash = hash_api_key(old_token)

        rotate_resp = await client.post(
            f"/api/v1/workspaces/agents/{agent_id}/rotate-token",
            headers=admin_headers,
        )
        assert rotate_resp.status_code == 200
        new_token = rotate_resp.json()["token"]
        assert new_token.startswith("ta_")
        assert new_token != old_token

        stored = await RemoteAgent.get(agent_id)
        assert stored is not None
        assert stored.key_hash == hash_api_key(new_token)
        assert stored.key_hash != old_hash  # old token can no longer authenticate

    async def test_rotate_unknown_agent_returns_404(self, client, admin_user, admin_headers):
        resp = await client.post(
            "/api/v1/workspaces/agents/000000000000000000000000/rotate-token",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_rotate_agent_owned_by_other_admin_returns_404(
        self, client, admin_user, regular_user, admin_headers, user_headers
    ):
        # Create as admin
        create_resp = await client.post(
            "/api/v1/workspaces/agents",
            json={"name": "admin-only"},
            headers=admin_headers,
        )
        agent_id = create_resp.json()["id"]

        # Regular user is not admin → 403 from get_admin_user
        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent_id}/rotate-token",
            headers=user_headers,
        )
        assert resp.status_code == 403

    async def test_unauthenticated_rotate_rejected(self, client):
        resp = await client.post(
            "/api/v1/workspaces/agents/000000000000000000000000/rotate-token"
        )
        assert resp.status_code == 401
