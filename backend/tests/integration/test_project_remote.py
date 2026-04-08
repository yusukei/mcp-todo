"""Integration tests for the project → remote-agent binding API.

The project ↔ remote agent binding lives in the embedded
``Project.remote`` field (a :class:`ProjectRemoteBinding`). It is
configured via ``PUT /api/v1/projects/{id}/remote`` and cleared via
``DELETE /api/v1/projects/{id}/remote``. Replaces the historical
``/api/v1/workspaces`` CRUD surface (removed 2026-04-08).
"""

import pytest_asyncio

from app.models import Project
from app.models.remote import RemoteAgent


@pytest_asyncio.fixture
async def agent_for_admin(admin_user):
    """Persist a RemoteAgent owned by the admin fixture."""
    agent = RemoteAgent(
        name="test-agent",
        key_hash="hash-admin",
        owner_id=str(admin_user.id),
    )
    await agent.insert()
    return agent


@pytest_asyncio.fixture
async def agent_for_regular(regular_user):
    """Persist a RemoteAgent owned by the regular user fixture."""
    agent = RemoteAgent(
        name="other-agent",
        key_hash="hash-regular",
        owner_id=str(regular_user.id),
    )
    await agent.insert()
    return agent


class TestSetProjectRemote:
    async def test_admin_can_bind_remote_to_project(
        self, client, admin_headers, agent_for_admin, test_project
    ):
        resp = await client.put(
            f"/api/v1/projects/{test_project.id}/remote",
            json={
                "agent_id": str(agent_for_admin.id),
                "remote_path": "/tmp/wsp",
                "label": "scratch",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["remote"] is not None
        assert body["remote"]["agent_id"] == str(agent_for_admin.id)
        assert body["remote"]["remote_path"] == "/tmp/wsp"
        assert body["remote"]["label"] == "scratch"

        # Verify persisted on the document
        reloaded = await Project.get(test_project.id)
        assert reloaded.remote is not None
        assert reloaded.remote.remote_path == "/tmp/wsp"

    async def test_bind_replaces_existing_binding(
        self, client, admin_headers, agent_for_admin, test_project
    ):
        first = await client.put(
            f"/api/v1/projects/{test_project.id}/remote",
            json={
                "agent_id": str(agent_for_admin.id),
                "remote_path": "/tmp/old",
                "label": "old",
            },
            headers=admin_headers,
        )
        assert first.status_code == 200

        second = await client.put(
            f"/api/v1/projects/{test_project.id}/remote",
            json={
                "agent_id": str(agent_for_admin.id),
                "remote_path": "/tmp/new",
                "label": "new",
            },
            headers=admin_headers,
        )
        assert second.status_code == 200
        body = second.json()
        assert body["remote"]["remote_path"] == "/tmp/new"
        assert body["remote"]["label"] == "new"

    async def test_unknown_agent_returns_404(
        self, client, admin_headers, test_project
    ):
        resp = await client.put(
            f"/api/v1/projects/{test_project.id}/remote",
            json={
                "agent_id": "000000000000000000000000",
                "remote_path": "/tmp/x",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_regular_user_cannot_bind_agent_they_do_not_own(
        self, client, user_headers, regular_user, agent_for_admin
    ):
        # Give the regular user access to a project
        from app.models.project import MemberRole, ProjectMember
        project = Project(
            name="regular-owned",
            created_by=regular_user,
            members=[
                ProjectMember(user_id=str(regular_user.id), role=MemberRole.owner)
            ],
            color="#888",
        )
        await project.insert()

        resp = await client.put(
            f"/api/v1/projects/{project.id}/remote",
            json={
                "agent_id": str(agent_for_admin.id),
                "remote_path": "/tmp/x",
            },
            headers=user_headers,
        )
        # agent_for_admin belongs to admin_user, not regular_user
        assert resp.status_code == 403

    async def test_owner_can_bind_own_agent(
        self, client, user_headers, regular_user, agent_for_regular
    ):
        from app.models.project import MemberRole, ProjectMember
        project = Project(
            name="regular-owned-2",
            created_by=regular_user,
            members=[
                ProjectMember(user_id=str(regular_user.id), role=MemberRole.owner)
            ],
            color="#888",
        )
        await project.insert()

        resp = await client.put(
            f"/api/v1/projects/{project.id}/remote",
            json={
                "agent_id": str(agent_for_regular.id),
                "remote_path": "/home/user/project",
            },
            headers=user_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["remote"]["remote_path"] == "/home/user/project"


class TestClearProjectRemote:
    async def test_delete_clears_binding(
        self, client, admin_headers, agent_for_admin, test_project
    ):
        await client.put(
            f"/api/v1/projects/{test_project.id}/remote",
            json={
                "agent_id": str(agent_for_admin.id),
                "remote_path": "/tmp/byebye",
            },
            headers=admin_headers,
        )

        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/remote",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["remote"] is None

        reloaded = await Project.get(test_project.id)
        assert reloaded.remote is None

    async def test_delete_when_not_bound_is_noop(
        self, client, admin_headers, test_project
    ):
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/remote",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["remote"] is None


class TestProjectResponseIncludesRemote:
    async def test_get_project_includes_remote_field(
        self, client, admin_headers, test_project
    ):
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}", headers=admin_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "remote" in body
        assert body["remote"] is None
