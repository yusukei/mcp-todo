"""Integration tests for /api/v1/workspaces CRUD endpoints.

Workspaces link a Project to a RemoteAgent + a remote directory. The
endpoints validate ownership, project existence, and uniqueness (one
project = one workspace).
"""

import pytest_asyncio

from app.models import Project
from app.models.remote import RemoteAgent, RemoteWorkspace


@pytest_asyncio.fixture
async def agent_for_admin(admin_user):
    """Persist a RemoteAgent owned by the admin fixture."""
    agent = RemoteAgent(
        name="test-agent",
        key_hash="hash-placeholder",
        owner_id=str(admin_user.id),
    )
    await agent.insert()
    return agent


@pytest_asyncio.fixture
async def project_for_admin(admin_user):
    """Persist a Project so workspace creation can find it."""
    from app.models.project import MemberRole, ProjectMember
    project = Project(
        name="test-project",
        description="",
        created_by=admin_user,
        members=[ProjectMember(user_id=str(admin_user.id), role=MemberRole.owner)],
        color="#888",
    )
    await project.insert()
    return project


class TestCreateWorkspace:
    async def test_admin_can_create_workspace(
        self, client, admin_user, admin_headers, agent_for_admin, project_for_admin
    ):
        resp = await client.post(
            "/api/v1/workspaces",
            json={
                "agent_id": str(agent_for_admin.id),
                "project_id": str(project_for_admin.id),
                "remote_path": "/tmp/wsp",
                "label": "scratch",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["agent_id"] == str(agent_for_admin.id)
        assert body["project_id"] == str(project_for_admin.id)
        assert body["remote_path"] == "/tmp/wsp"
        assert body["label"] == "scratch"
        assert body["agent_name"] == "test-agent"
        assert body["project_name"] == "test-project"

    async def test_create_with_unknown_agent_returns_404(
        self, client, admin_user, admin_headers, project_for_admin
    ):
        resp = await client.post(
            "/api/v1/workspaces",
            json={
                "agent_id": "000000000000000000000000",
                "project_id": str(project_for_admin.id),
                "remote_path": "/tmp/x",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_create_with_unknown_project_returns_404(
        self, client, admin_user, admin_headers, agent_for_admin
    ):
        resp = await client.post(
            "/api/v1/workspaces",
            json={
                "agent_id": str(agent_for_admin.id),
                "project_id": "000000000000000000000000",
                "remote_path": "/tmp/x",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_duplicate_project_workspace_returns_409(
        self, client, admin_user, admin_headers, agent_for_admin, project_for_admin
    ):
        body = {
            "agent_id": str(agent_for_admin.id),
            "project_id": str(project_for_admin.id),
            "remote_path": "/tmp/dup",
        }
        first = await client.post(
            "/api/v1/workspaces", json=body, headers=admin_headers
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/workspaces", json=body, headers=admin_headers
        )
        assert second.status_code == 409

    async def test_regular_user_cannot_create(
        self, client, regular_user, user_headers, agent_for_admin, project_for_admin
    ):
        resp = await client.post(
            "/api/v1/workspaces",
            json={
                "agent_id": str(agent_for_admin.id),
                "project_id": str(project_for_admin.id),
                "remote_path": "/tmp/x",
            },
            headers=user_headers,
        )
        assert resp.status_code == 403


class TestListWorkspaces:
    async def test_list_returns_created_workspace(
        self, client, admin_user, admin_headers, agent_for_admin, project_for_admin
    ):
        await client.post(
            "/api/v1/workspaces",
            json={
                "agent_id": str(agent_for_admin.id),
                "project_id": str(project_for_admin.id),
                "remote_path": "/tmp/listme",
            },
            headers=admin_headers,
        )

        resp = await client.get("/api/v1/workspaces", headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()
        assert any(w["remote_path"] == "/tmp/listme" for w in items)

    async def test_list_empty_when_none(self, client, admin_user, admin_headers):
        resp = await client.get("/api/v1/workspaces", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json() == []


class TestUpdateWorkspace:
    async def test_update_remote_path_and_label(
        self, client, admin_user, admin_headers, agent_for_admin, project_for_admin
    ):
        create = await client.post(
            "/api/v1/workspaces",
            json={
                "agent_id": str(agent_for_admin.id),
                "project_id": str(project_for_admin.id),
                "remote_path": "/tmp/old",
                "label": "old",
            },
            headers=admin_headers,
        )
        wid = create.json()["id"]

        resp = await client.patch(
            f"/api/v1/workspaces/{wid}",
            json={"remote_path": "/tmp/new", "label": "new"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["remote_path"] == "/tmp/new"
        assert body["label"] == "new"

    async def test_update_unknown_workspace_returns_404(
        self, client, admin_user, admin_headers
    ):
        resp = await client.patch(
            "/api/v1/workspaces/000000000000000000000000",
            json={"remote_path": "/tmp/x"},
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestDeleteWorkspace:
    async def test_delete_removes_workspace(
        self, client, admin_user, admin_headers, agent_for_admin, project_for_admin
    ):
        create = await client.post(
            "/api/v1/workspaces",
            json={
                "agent_id": str(agent_for_admin.id),
                "project_id": str(project_for_admin.id),
                "remote_path": "/tmp/byebye",
            },
            headers=admin_headers,
        )
        wid = create.json()["id"]

        resp = await client.delete(
            f"/api/v1/workspaces/{wid}", headers=admin_headers
        )
        assert resp.status_code == 204

        gone = await RemoteWorkspace.get(wid)
        assert gone is None

    async def test_delete_unknown_returns_404(self, client, admin_user, admin_headers):
        resp = await client.delete(
            "/api/v1/workspaces/000000000000000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404
