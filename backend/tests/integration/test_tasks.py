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
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 2

    async def test_deleted_tasks_excluded(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user)
        await make_task(str(test_project.id), admin_user, is_deleted=True)

        resp = await client.get(_task_url(str(test_project.id)), headers=admin_headers)
        assert len(resp.json()["items"]) == 1

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
        tasks = resp.json()["items"]
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
        tasks = resp.json()["items"]
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
        tasks = resp.json()["items"]
        assert len(tasks) == 1
        assert "bug" in tasks[0]["tags"]

    async def test_filter_by_needs_detail(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, needs_detail=True)
        await make_task(str(test_project.id), admin_user, needs_detail=False)

        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"needs_detail": "true"},
            headers=admin_headers,
        )
        tasks = resp.json()["items"]
        assert len(tasks) == 1
        assert tasks[0]["needs_detail"] is True

    async def test_filter_by_approved(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, approved=True)
        await make_task(str(test_project.id), admin_user, approved=False)

        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"approved": "true"},
            headers=admin_headers,
        )
        tasks = resp.json()["items"]
        assert len(tasks) == 1
        assert tasks[0]["approved"] is True

    async def test_pagination_limit_and_skip(
        self, client, admin_user, test_project, admin_headers
    ):
        for i in range(5):
            await make_task(str(test_project.id), admin_user, title=f"Task {i}")

        # limit=2
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"limit": 2},
            headers=admin_headers,
        )
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["limit"] == 2
        assert data["skip"] == 0

        # skip=3, limit=2
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"limit": 2, "skip": 3},
            headers=admin_headers,
        )
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["skip"] == 3

        # skip past all
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"skip": 10},
            headers=admin_headers,
        )
        data = resp.json()
        assert len(data["items"]) == 0
        assert data["total"] == 5

    async def test_pagination_invalid_params(
        self, client, test_project, admin_headers
    ):
        # limit=0 should fail (ge=1)
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"limit": 0},
            headers=admin_headers,
        )
        assert resp.status_code == 422

        # limit=300 should fail (le=200)
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"limit": 300},
            headers=admin_headers,
        )
        assert resp.status_code == 422

        # skip=-1 should fail (ge=0)
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"skip": -1},
            headers=admin_headers,
        )
        assert resp.status_code == 422

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

    async def test_update_needs_detail_flag(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"needs_detail": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_detail"] is True
        assert data["approved"] is False

    async def test_update_approved_flag(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"approved": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True
        assert data["needs_detail"] is False

    async def test_approved_clears_needs_detail(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user, needs_detail=True)

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"approved": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True
        assert data["needs_detail"] is False

    async def test_needs_detail_clears_approved(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user, approved=True)

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"needs_detail": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_detail"] is True
        assert data["approved"] is False

    async def test_new_task_defaults_flags_to_false(
        self, client, admin_user, test_project, admin_headers
    ):
        resp = await client.post(
            _task_url(str(test_project.id)),
            json={"title": "New Task"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["needs_detail"] is False
        assert data["approved"] is False

    async def test_clear_due_date_with_null(
        self, client, admin_user, test_project, admin_headers
    ):
        """Sending due_date=null should clear the due date."""
        task = await make_task(
            str(test_project.id), admin_user,
            due_date=datetime(2025, 12, 31, tzinfo=UTC),
        )
        assert task.due_date is not None

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"due_date": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["due_date"] is None

    async def test_clear_assignee_id_with_null(
        self, client, admin_user, test_project, admin_headers
    ):
        """Sending assignee_id=null should clear the assignee."""
        task = await make_task(str(test_project.id), admin_user)
        task.assignee_id = str(admin_user.id)
        await task.save()

        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"assignee_id": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["assignee_id"] is None

    async def test_omitted_fields_are_not_changed(
        self, client, admin_user, test_project, admin_headers
    ):
        """Fields not included in the PATCH body should remain unchanged."""
        task = await make_task(
            str(test_project.id), admin_user,
            due_date=datetime(2025, 6, 15, tzinfo=UTC),
        )
        task.assignee_id = str(admin_user.id)
        await task.save()

        # Only update title - due_date and assignee_id should remain
        resp = await client.patch(
            _task_url(str(test_project.id), str(task.id)),
            json={"title": "Updated Title"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated Title"
        assert data["due_date"] is not None
        assert data["assignee_id"] == str(admin_user.id)

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


class TestArchive:
    async def test_archive_task(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/archive",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["archived"] is True

    async def test_unarchive_task(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user, archived=True)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/unarchive",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["archived"] is False

    async def test_archive_deleted_task_returns_404(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user, is_deleted=True)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/archive",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_archive_nonexistent_task_returns_404(
        self, client, test_project, admin_headers
    ):
        resp = await client.post(
            f"{_task_url(str(test_project.id), '000000000000000000000000')}/archive",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_filter_by_archived(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, title="Active", archived=False)
        await make_task(str(test_project.id), admin_user, title="Archived", archived=True)

        # Filter archived=false
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"archived": "false"},
            headers=admin_headers,
        )
        tasks = resp.json()["items"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Active"

        # Filter archived=true
        resp = await client.get(
            _task_url(str(test_project.id)),
            params={"archived": "true"},
            headers=admin_headers,
        )
        tasks = resp.json()["items"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Archived"

        # No filter returns all
        resp = await client.get(
            _task_url(str(test_project.id)),
            headers=admin_headers,
        )
        tasks = resp.json()["items"]
        assert len(tasks) == 2

    async def test_archived_field_in_task_response(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.get(
            _task_url(str(test_project.id), str(task.id)),
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert "archived" in resp.json()
        assert resp.json()["archived"] is False


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
            f"{_task_url(str(test_project.id), str(task.id))}/comments/000000000000000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404
