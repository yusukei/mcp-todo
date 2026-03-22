"""タスクエンドポイントの統合テスト"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models import Task, User
from app.models.task import TaskPriority, TaskStatus
from app.core.security import create_access_token
from tests.helpers.factories import make_task


def _task_url(project_id: str, task_id: str | None = None) -> str:
    base = f"/api/v1/projects/{project_id}/tasks"
    return f"{base}/{task_id}" if task_id else base


class TestListTasks:
    async def test_member_can_list_tasks(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, title="Task A")
        await make_task(str(test_project.id), admin_user, title="Task B")

        resp = await client.get(_task_url(str(test_project.id)), headers=admin_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_deleted_tasks_excluded(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user)
        await make_task(str(test_project.id), admin_user, is_deleted=True)

        resp = await client.get(_task_url(str(test_project.id)), headers=admin_headers)
        assert len(resp.json()) == 1

    async def test_filter_by_status(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, status=TaskStatus.todo)
        await make_task(str(test_project.id), admin_user, status=TaskStatus.done)

        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"status": "todo"},
            headers=admin_headers,
        )
        tasks = resp.json()
        assert all(t["status"] == "todo" for t in tasks)
        assert len(tasks) == 1

    async def test_filter_by_priority(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, priority=TaskPriority.urgent)
        await make_task(str(test_project.id), admin_user, priority=TaskPriority.low)

        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"priority": "urgent"},
            headers=admin_headers,
        )
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["priority"] == "urgent"

    async def test_filter_by_tag(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, tags=["bug"])
        await make_task(str(test_project.id), admin_user, tags=["feature"])

        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"tag": "bug"},
            headers=admin_headers,
        )
        tasks = resp.json()
        assert len(tasks) == 1
        assert "bug" in tasks[0]["tags"]

    async def test_non_member_cannot_list(
        self, client, test_project
    ):
        outsider = User(
            email="x@test.com", name="X", auth_type="google", is_active=True
        )
        await outsider.insert()
        token = create_access_token(str(outsider.id))

        resp = await client.get(
            _task_url(str(test_project.id)),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_unauthenticated_returns_401(self, client, test_project):
        resp = await client.get(_task_url(str(test_project.id)))
        assert resp.status_code == 401


class TestCreateTask:
    async def test_member_can_create_task(
        self, client, admin_user, test_project, admin_headers
    ):
        resp = await client.post(
            _task_url(str(test_project.id)),
            json={"title": "New Task", "priority": "high", "tags": ["backend"]},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "New Task"
        assert data["priority"] == "high"
        assert data["created_by"] == str(admin_user.id)

    async def test_create_task_missing_title_fails(
        self, client, test_project, admin_headers
    ):
        resp = await client.post(
            _task_url(str(test_project.id)),
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_create_task_nonexistent_project_returns_404(
        self, client, admin_headers
    ):
        resp = await client.post(
            "/api/v1/projects/000000000000000000000000/tasks",
            json={"title": "Task"},
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestGetTask:
    async def test_member_can_get_task(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.get(
            _task_url(str(test_project.id), str(task.id)),
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == str(task.id)

    async def test_deleted_task_returns_404(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user, is_deleted=True)

        resp = await client.get(
            _task_url(str(test_project.id), str(task.id)),
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_task_from_different_project_returns_404(
        self, client, admin_user, test_project, admin_headers
    ):
        """別プロジェクトのタスク ID で取得するとプロジェクト境界が守られる"""
        other_project_id = "000000000000000000000001"
        task = await make_task(other_project_id, admin_user)

        resp = await client.get(
            _task_url(str(test_project.id), str(task.id)),
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestUpdateTask:
    async def test_update_task_fields(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user, title="Old Title")

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"title": "New Title", "priority": "urgent"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Title"
        assert data["priority"] == "urgent"

    async def test_set_status_to_done_sets_completed_at(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)
        assert task.completed_at is None

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"status": "done"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["completed_at"] is not None

    async def test_reset_status_from_done_clears_completed_at(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(
            str(test_project.id), admin_user, status=TaskStatus.done
        )
        task.completed_at = datetime.now(UTC)
        await task.save()

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"status": "in_progress"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["completed_at"] is None

    async def test_non_member_cannot_update(
        self, client, admin_user, test_project
    ):
        task = await make_task(str(test_project.id), admin_user)
        outsider = User(
            email="x@test.com", name="X", auth_type="google", is_active=True
        )
        await outsider.insert()
        token = create_access_token(str(outsider.id))

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"title": "Hacked"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


class TestDeleteTask:
    async def test_logical_delete(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.delete(
            _task_url(str(test_project.id), str(task.id)),
            headers=admin_headers,
        )
        assert resp.status_code == 204

        # 論理削除後は 404
        resp2 = await client.get(
            _task_url(str(test_project.id), str(task.id)),
            headers=admin_headers,
        )
        assert resp2.status_code == 404

        # DB には残っている (is_deleted=True)
        db_task = await Task.get(task.id)
        assert db_task is not None
        assert db_task.is_deleted is True

    async def test_delete_nonexistent_returns_404(
        self, client, test_project, admin_headers
    ):
        resp = await client.delete(
            _task_url(str(test_project.id), "000000000000000000000000"),
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestCompleteAndReopen:
    async def test_complete_task_sets_status_and_timestamp(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/complete",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["completed_at"] is not None

    async def test_complete_deleted_task_returns_404(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user, is_deleted=True)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/complete",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_reopen_done_task(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(
            str(test_project.id), admin_user, status=TaskStatus.done
        )
        task.completed_at = datetime.now(UTC)
        await task.save()

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/reopen",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "todo"
        assert data["completed_at"] is None

    async def test_reopen_cancelled_task(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(
            str(test_project.id), admin_user, status=TaskStatus.cancelled
        )

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/reopen",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "todo"


class TestComments:
    async def test_add_comment_to_task(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/comments",
            json={"content": "This is a comment"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        comments = data["comments"]
        assert len(comments) == 1
        assert comments[0]["content"] == "This is a comment"
        assert comments[0]["author_id"] == str(admin_user.id)
        assert comments[0]["author_name"] == admin_user.name

    async def test_add_empty_comment_fails(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/comments",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_delete_comment(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)
        # コメント追加
        add_resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/comments",
            json={"content": "To be deleted"},
            headers=admin_headers,
        )
        comment_id = add_resp.json()["comments"][0]["id"]

        # コメント削除
        del_resp = await client.delete(
            f"{_task_url(str(test_project.id), str(task.id))}/comments/{comment_id}",
            headers=admin_headers,
        )
        assert del_resp.status_code == 204

    async def test_delete_nonexistent_comment_returns_404(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.delete(
            f"{_task_url(str(test_project.id), str(task.id))}/comments/nonexistent-id",
            headers=admin_headers,
        )
        assert resp.status_code == 404
