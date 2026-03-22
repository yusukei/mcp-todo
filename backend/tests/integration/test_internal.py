"""内部 API エンドポイント (/api/v1/internal/*) の統合テスト

MCP コンテナからの X-MCP-Internal-Secret ヘッダー認証を使う。
"""

import pytest
import pytest_asyncio

from app.core.security import hash_api_key
from app.models import McpApiKey, Project, Task, User
from app.models.project import ProjectMember
from app.models.task import TaskPriority, TaskStatus
from app.models.user import AuthType
from tests.helpers.factories import make_task

MCP_SECRET = "test-mcp-secret-for-testing"


@pytest.fixture
def mcp_headers():
    return {"X-MCP-Internal-Secret": MCP_SECRET}


@pytest_asyncio.fixture
async def mcp_api_key(admin_user):
    """DB に保存された MCP API キー (resolve_api_key テスト用)"""
    raw_key = "mtodo_test_key_for_integration"
    key = McpApiKey(
        key_hash=hash_api_key(raw_key),
        name="Test Key",
        project_scopes=["proj-scope-1", "proj-scope-2"],
        created_by=admin_user,
    )
    await key.insert()
    return raw_key, key


class TestResolveApiKey:
    async def test_valid_key_returns_key_id_and_scopes(
        self, client, mcp_headers, mcp_api_key
    ):
        raw_key, db_key = mcp_api_key
        resp = await client.post(
            "/api/v1/internal/auth/api-key",
            json={"key": raw_key},
            headers=mcp_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_id"] == str(db_key.id)
        assert data["project_scopes"] == ["proj-scope-1", "proj-scope-2"]

    async def test_invalid_key_returns_401(self, client, mcp_headers):
        resp = await client.post(
            "/api/v1/internal/auth/api-key",
            json={"key": "mtodo_nonexistent_key"},
            headers=mcp_headers,
        )
        assert resp.status_code == 401


class TestListProjects:
    async def test_returns_projects(
        self, client, admin_user, test_project, mcp_headers
    ):
        resp = await client.get("/api/v1/internal/projects", headers=mcp_headers)
        assert resp.status_code == 200
        projects = resp.json()
        assert len(projects) >= 1
        names = [p["name"] for p in projects]
        assert "Test Project" in names


class TestGetProject:
    async def test_returns_project(self, client, test_project, mcp_headers):
        resp = await client.get(
            f"/api/v1/internal/projects/{test_project.id}", headers=mcp_headers
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Project"

    async def test_nonexistent_returns_404(self, client, mcp_headers):
        resp = await client.get(
            "/api/v1/internal/projects/000000000000000000000000",
            headers=mcp_headers,
        )
        assert resp.status_code == 404


class TestListTasks:
    async def test_returns_tasks(
        self, client, admin_user, test_project, mcp_headers
    ):
        await make_task(str(test_project.id), admin_user, title="Task A")
        await make_task(str(test_project.id), admin_user, title="Task B")

        resp = await client.get(
            f"/api/v1/internal/projects/{test_project.id}/tasks",
            headers=mcp_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_filter_by_task_status(
        self, client, admin_user, test_project, mcp_headers
    ):
        await make_task(str(test_project.id), admin_user, status=TaskStatus.todo)
        await make_task(str(test_project.id), admin_user, status=TaskStatus.done)

        resp = await client.get(
            f"/api/v1/internal/projects/{test_project.id}/tasks",
            params={"task_status": "todo"},
            headers=mcp_headers,
        )
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["status"] == "todo"


class TestCreateTask:
    async def test_creates_task(
        self, client, test_project, mcp_headers
    ):
        resp = await client.post(
            f"/api/v1/internal/projects/{test_project.id}/tasks",
            json={"title": "MCP Task", "priority": "high", "tags": ["mcp"]},
            headers=mcp_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "MCP Task"
        assert data["priority"] == "high"
        assert "mcp" in data["tags"]


class TestGetTask:
    async def test_returns_task(
        self, client, admin_user, test_project, mcp_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.get(
            f"/api/v1/internal/tasks/{task.id}", headers=mcp_headers
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == str(task.id)

    async def test_deleted_task_returns_404(
        self, client, admin_user, test_project, mcp_headers
    ):
        task = await make_task(str(test_project.id), admin_user, is_deleted=True)

        resp = await client.get(
            f"/api/v1/internal/tasks/{task.id}", headers=mcp_headers
        )
        assert resp.status_code == 404


class TestUpdateTask:
    async def test_updates_task(
        self, client, admin_user, test_project, mcp_headers
    ):
        task = await make_task(str(test_project.id), admin_user, title="Old")

        resp = await client.patch(
            f"/api/v1/internal/tasks/{task.id}",
            json={"title": "New", "priority": "urgent"},
            headers=mcp_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New"
        assert data["priority"] == "urgent"

    async def test_update_nonexistent_returns_404(self, client, mcp_headers):
        resp = await client.patch(
            "/api/v1/internal/tasks/000000000000000000000000",
            json={"title": "X"},
            headers=mcp_headers,
        )
        assert resp.status_code == 404


class TestDeleteTask:
    async def test_deletes_task(
        self, client, admin_user, test_project, mcp_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.delete(
            f"/api/v1/internal/tasks/{task.id}", headers=mcp_headers
        )
        assert resp.status_code == 204

        # 削除後は取得不可
        resp2 = await client.get(
            f"/api/v1/internal/tasks/{task.id}", headers=mcp_headers
        )
        assert resp2.status_code == 404

    async def test_delete_nonexistent_returns_404(self, client, mcp_headers):
        resp = await client.delete(
            "/api/v1/internal/tasks/000000000000000000000000",
            headers=mcp_headers,
        )
        assert resp.status_code == 404


class TestListUsers:
    async def test_returns_active_users(
        self, client, admin_user, regular_user, mcp_headers
    ):
        resp = await client.get("/api/v1/internal/users", headers=mcp_headers)
        assert resp.status_code == 200
        users = resp.json()
        emails = [u["email"] for u in users]
        assert "admin@test.com" in emails
        assert "user@test.com" in emails

    async def test_excludes_inactive_users(
        self, client, admin_user, inactive_user, mcp_headers
    ):
        resp = await client.get("/api/v1/internal/users", headers=mcp_headers)
        assert resp.status_code == 200
        emails = [u["email"] for u in resp.json()]
        assert "inactive@test.com" not in emails


class TestMcpSecretRequired:
    """全エンドポイントが MCP シークレットなしで 403 を返すことを確認"""

    async def test_resolve_api_key_without_secret(self, client):
        resp = await client.post(
            "/api/v1/internal/auth/api-key", json={"key": "x"}
        )
        assert resp.status_code == 422  # Header required → validation error

    async def test_list_projects_without_secret(self, client):
        resp = await client.get("/api/v1/internal/projects")
        assert resp.status_code == 422

    async def test_get_project_without_secret(self, client):
        resp = await client.get("/api/v1/internal/projects/000000000000000000000000")
        assert resp.status_code == 422

    async def test_list_tasks_without_secret(self, client):
        resp = await client.get(
            "/api/v1/internal/projects/000000000000000000000000/tasks"
        )
        assert resp.status_code == 422

    async def test_create_task_without_secret(self, client):
        resp = await client.post(
            "/api/v1/internal/projects/000000000000000000000000/tasks",
            json={"title": "X"},
        )
        assert resp.status_code == 422

    async def test_get_task_without_secret(self, client):
        resp = await client.get("/api/v1/internal/tasks/000000000000000000000000")
        assert resp.status_code == 422

    async def test_update_task_without_secret(self, client):
        resp = await client.patch(
            "/api/v1/internal/tasks/000000000000000000000000",
            json={"title": "X"},
        )
        assert resp.status_code == 422

    async def test_delete_task_without_secret(self, client):
        resp = await client.delete(
            "/api/v1/internal/tasks/000000000000000000000000"
        )
        assert resp.status_code == 422

    async def test_list_users_without_secret(self, client):
        resp = await client.get("/api/v1/internal/users")
        assert resp.status_code == 422

    async def test_wrong_secret_returns_403(self, client):
        resp = await client.get(
            "/api/v1/internal/users",
            headers={"X-MCP-Internal-Secret": "wrong-secret"},
        )
        assert resp.status_code == 403
