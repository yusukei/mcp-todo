"""プロジェクトエンドポイントの統合テスト"""

import os
import pytest

from app.models import Project, Task, User
from app.models.project import MemberRole, ProjectMember, ProjectStatus
from app.models.task import TaskStatus
from app.core.security import create_access_token

# mongomock-motor はネストフィールドクエリの互換性が不完全なためスキップマーク
needs_real = pytest.mark.skipif(
    os.environ.get("TEST_MODE", "mock") == "mock",
    reason="mongomock-motor does not fully support nested field queries; run with TEST_MODE=real",
)


class TestListProjects:
    async def test_admin_sees_all_active_projects(
        self, client, admin_user, regular_user, test_project, admin_headers
    ):
        resp = await client.get("/api/v1/projects", headers=admin_headers)
        assert resp.status_code == 200
        projects = resp.json()
        assert len(projects) == 1
        assert projects[0]["name"] == "Test Project"

    async def test_admin_does_not_see_archived_projects(
        self, client, admin_user, admin_headers
    ):
        archived = Project(
            name="Archived",
            created_by=admin_user,
            members=[ProjectMember(user_id=str(admin_user.id))],
            status=ProjectStatus.archived,
        )
        await archived.insert()

        resp = await client.get("/api/v1/projects", headers=admin_headers)
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert "Archived" not in names

    @needs_real
    async def test_regular_user_sees_only_member_projects(
        self, client, admin_user, regular_user, test_project, user_headers
    ):
        """一般ユーザーは自分がメンバーのプロジェクトのみ取得"""
        other = Project(
            name="Other Project",
            created_by=admin_user,
            members=[ProjectMember(user_id=str(admin_user.id))],
        )
        await other.insert()

        resp = await client.get("/api/v1/projects", headers=user_headers)
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert "Test Project" in names
        assert "Other Project" not in names

    async def test_unauthenticated_returns_401(self, client):
        resp = await client.get("/api/v1/projects")
        assert resp.status_code == 401


class TestCreateProject:
    async def test_admin_can_create_project(self, client, admin_user, admin_headers):
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "New Project", "description": "desc", "color": "#ff0000"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Project"
        assert data["color"] == "#ff0000"
        # 作成者がメンバーに含まれる
        member_ids = [m["user_id"] for m in data["members"]]
        assert str(admin_user.id) in member_ids

    async def test_regular_user_can_create_project(
        self, client, regular_user, user_headers
    ):
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "My Project"},
            headers=user_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert any(
            m["user_id"] == str(regular_user.id) and m["role"] == "owner"
            for m in data["members"]
        )

    async def test_create_project_without_name_fails(self, client, admin_headers):
        resp = await client.post(
            "/api/v1/projects", json={}, headers=admin_headers
        )
        assert resp.status_code == 422

    async def test_unauthenticated_cannot_create(self, client):
        resp = await client.post("/api/v1/projects", json={"name": "X"})
        assert resp.status_code == 401


class TestGetProject:
    async def test_admin_can_get_any_project(
        self, client, test_project, admin_headers
    ):
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Project"

    async def test_member_can_get_project(
        self, client, test_project, user_headers
    ):
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}", headers=user_headers
        )
        assert resp.status_code == 200

    async def test_non_member_cannot_get_project(
        self, client, admin_user, test_project
    ):
        outsider = User(
            email="outsider@test.com",
            name="Outsider",
            auth_type="google",
            is_active=True,
        )
        await outsider.insert()
        token = create_access_token(str(outsider.id))

        resp = await client.get(
            f"/api/v1/projects/{test_project.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_nonexistent_project_returns_404(self, client, admin_headers):
        resp = await client.get(
            "/api/v1/projects/000000000000000000000000", headers=admin_headers
        )
        assert resp.status_code == 404


class TestUpdateProject:
    async def test_admin_can_update_project(
        self, client, test_project, admin_headers
    ):
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}",
            json={"name": "Updated Name", "color": "#00ff00"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Name"
        assert data["color"] == "#00ff00"

    async def test_admin_can_update_all_fields(
        self, client, test_project, admin_headers
    ):
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}",
            json={
                "description": "New desc",
                "status": "archived",
                "is_locked": True,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "New desc"
        assert data["status"] == "archived"
        assert data["is_locked"] is True

    async def test_regular_user_cannot_update_project(
        self, client, test_project, user_headers
    ):
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}",
            json={"name": "Hacked"},
            headers=user_headers,
        )
        assert resp.status_code == 403

    async def test_update_nonexistent_project_returns_404(
        self, client, admin_headers
    ):
        resp = await client.patch(
            "/api/v1/projects/000000000000000000000000",
            json={"name": "X"},
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestDeleteProject:
    async def test_admin_can_delete_project(
        self, client, test_project, admin_headers
    ):
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}", headers=admin_headers
        )
        assert resp.status_code == 204

        # ソフトデリート後はアーカイブ状態
        resp2 = await client.get(
            f"/api/v1/projects/{test_project.id}", headers=admin_headers
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "archived"

    async def test_delete_project_soft_deletes_tasks(
        self, client, admin_user, test_project, admin_headers
    ):
        task = Task(
            project_id=str(test_project.id),
            title="Task to delete",
            created_by=str(admin_user.id),
        )
        await task.insert()

        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}", headers=admin_headers
        )
        assert resp.status_code == 204

        updated_task = await Task.get(task.id)
        assert updated_task.is_deleted is True

    async def test_regular_user_cannot_delete_project(
        self, client, test_project, user_headers
    ):
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}", headers=user_headers
        )
        assert resp.status_code == 403


class TestMemberManagement:
    async def test_admin_can_add_member(
        self, client, admin_user, test_project, admin_headers
    ):
        new_user = User(
            email="new@test.com",
            name="New",
            auth_type="google",
            is_active=True,
        )
        await new_user.insert()

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/members",
            json={"user_id": str(new_user.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        member_ids = [m["user_id"] for m in resp.json()["members"]]
        assert str(new_user.id) in member_ids

    async def test_add_duplicate_member_returns_409(
        self, client, admin_user, regular_user, test_project, admin_headers
    ):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/members",
            json={"user_id": str(regular_user.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 409

    async def test_admin_can_remove_member(
        self, client, regular_user, test_project, admin_headers
    ):
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/members/{regular_user.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 204

    async def test_cannot_remove_last_owner(
        self, client, admin_user, admin_headers
    ):
        """最後のオーナーは削除できない"""
        project = Project(
            name="Single Owner",
            created_by=admin_user,
            members=[ProjectMember(user_id=str(admin_user.id), role=MemberRole.owner)],
        )
        await project.insert()

        resp = await client.delete(
            f"/api/v1/projects/{project.id}/members/{admin_user.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert "last owner" in resp.json()["detail"].lower()

    async def test_remove_nonexistent_member_returns_404(
        self, client, test_project, admin_headers
    ):
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/members/000000000000000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_regular_user_cannot_add_member(
        self, client, test_project, user_headers
    ):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/members",
            json={"user_id": "anyid"},
            headers=user_headers,
        )
        assert resp.status_code == 403


class TestUpdateMemberRole:
    async def test_admin_can_change_member_to_owner(
        self, client, regular_user, test_project, admin_headers
    ):
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/members/{regular_user.id}",
            json={"role": "owner"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        member = next(m for m in resp.json()["members"] if m["user_id"] == str(regular_user.id))
        assert member["role"] == "owner"

    async def test_admin_can_demote_owner_to_member(
        self, client, admin_user, admin_headers
    ):
        """オーナーが複数いる場合、降格できる"""
        second_owner = User(
            email="owner2@test.com", name="Owner2", auth_type="google", is_active=True
        )
        await second_owner.insert()
        project = Project(
            name="Multi Owner",
            created_by=admin_user,
            members=[
                ProjectMember(user_id=str(admin_user.id), role=MemberRole.owner),
                ProjectMember(user_id=str(second_owner.id), role=MemberRole.owner),
            ],
        )
        await project.insert()

        resp = await client.patch(
            f"/api/v1/projects/{project.id}/members/{second_owner.id}",
            json={"role": "member"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        member = next(m for m in resp.json()["members"] if m["user_id"] == str(second_owner.id))
        assert member["role"] == "member"

    async def test_cannot_demote_last_owner(
        self, client, admin_user, admin_headers
    ):
        """最後のオーナーは降格できない"""
        project = Project(
            name="Solo Owner",
            created_by=admin_user,
            members=[ProjectMember(user_id=str(admin_user.id), role=MemberRole.owner)],
        )
        await project.insert()

        resp = await client.patch(
            f"/api/v1/projects/{project.id}/members/{admin_user.id}",
            json={"role": "member"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert "last owner" in resp.json()["detail"].lower()

    async def test_nonexistent_member_returns_404(
        self, client, test_project, admin_headers
    ):
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/members/000000000000000000000000",
            json={"role": "owner"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_regular_user_cannot_change_role(
        self, client, regular_user, test_project, user_headers
    ):
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/members/{regular_user.id}",
            json={"role": "owner"},
            headers=user_headers,
        )
        assert resp.status_code == 403


class TestProjectSummary:
    async def test_summary_empty_project(
        self, client, test_project, admin_headers
    ):
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/summary", headers=admin_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["completion_rate"] == 0

    async def test_summary_counts_tasks_by_status(
        self, client, admin_user, test_project, admin_headers
    ):
        from tests.helpers.factories import make_task
        from app.models.task import TaskStatus

        await make_task(str(test_project.id), admin_user, status=TaskStatus.todo)
        await make_task(str(test_project.id), admin_user, status=TaskStatus.done)
        await make_task(str(test_project.id), admin_user, status=TaskStatus.done)

        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/summary", headers=admin_headers
        )
        data = resp.json()
        assert data["total"] == 3
        assert data["by_status"]["done"] == 2
        assert data["completion_rate"] == pytest.approx(66.7)

    async def test_summary_excludes_deleted_tasks(
        self, client, admin_user, test_project, admin_headers
    ):
        from tests.helpers.factories import make_task

        await make_task(str(test_project.id), admin_user)
        await make_task(str(test_project.id), admin_user, is_deleted=True)

        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/summary", headers=admin_headers
        )
        assert resp.json()["total"] == 1

    async def test_get_project_non_member_returns_403(
        self, client, test_project
    ):
        outsider = User(
            email="out@test.com", name="Out", auth_type="google", is_active=True
        )
        await outsider.insert()
        token = create_access_token(str(outsider.id))

        resp = await client.get(
            f"/api/v1/projects/{test_project.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_summary_non_member_returns_403(
        self, client, test_project
    ):
        outsider = User(
            email="x@test.com", name="X", auth_type="google", is_active=True
        )
        await outsider.insert()
        token = create_access_token(str(outsider.id))

        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
