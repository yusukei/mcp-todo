"""MCP tool unit tests for reopen_task, delete_comment, get_subtasks, list_tags, list_tasks date filters."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Task
from app.models.task import Comment, TaskStatus
from tests.helpers.factories import make_task


@pytest.fixture
def mock_auth():
    """Mock authenticate() to return a key_info dict with no project scopes."""
    with patch(
        "app.mcp.tools.tasks.authenticate",
        new_callable=AsyncMock,
        return_value={"key_id": "test-key", "project_scopes": []},
    ) as m:
        yield m


@pytest.fixture
def mock_check():
    """Mock check_project_access() to always pass."""
    with patch("app.mcp.tools.tasks.check_project_access") as m:
        yield m


@pytest.fixture
def mock_publish():
    """Mock publish_event() to avoid Redis dependency."""
    with patch(
        "app.mcp.tools.tasks.publish_event",
        new_callable=AsyncMock,
    ) as m:
        yield m


# Shared mock for authenticate + check_project_access
_MOCK_KEY_INFO = {"key_id": "test-key", "project_scopes": []}


def _patch_mcp_auth():
    """Patch authenticate() and check_project_access() for MCP tool tests."""
    return [
        patch(
            "app.mcp.tools.tasks.authenticate",
            new_callable=AsyncMock,
            return_value=_MOCK_KEY_INFO,
        ),
        patch("app.mcp.tools.tasks.check_project_access"),
    ]


class TestReopenTask:
    async def test_reopen_done_task(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Reopening a done task sets status to todo and clears completed_at."""
        from app.mcp.tools.tasks import reopen_task

        task = await make_task(
            str(test_project.id), admin_user, status=TaskStatus.done
        )
        task.completed_at = datetime.now(UTC)
        await task.save()

        result = await reopen_task.fn(task_id=str(task.id))

        assert result["status"] == "todo"
        assert result["completed_at"] is None

        db_task = await Task.get(task.id)
        assert db_task.status == TaskStatus.todo
        assert db_task.completed_at is None

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "task.updated"

    async def test_reopen_cancelled_task(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Reopening a cancelled task sets status to todo."""
        from app.mcp.tools.tasks import reopen_task

        task = await make_task(
            str(test_project.id), admin_user, status=TaskStatus.cancelled
        )

        result = await reopen_task.fn(task_id=str(task.id))

        assert result["status"] == "todo"
        assert result["completed_at"] is None

    async def test_reopen_already_todo_task(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Reopening a todo task is idempotent - still returns todo."""
        from app.mcp.tools.tasks import reopen_task

        task = await make_task(
            str(test_project.id), admin_user, status=TaskStatus.todo
        )

        result = await reopen_task.fn(task_id=str(task.id))

        assert result["status"] == "todo"

    async def test_reopen_deleted_task_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Reopening a soft-deleted task raises ToolError."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import reopen_task

        task = await make_task(
            str(test_project.id), admin_user, is_deleted=True
        )

        with pytest.raises(ToolError, match="Task not found"):
            await reopen_task.fn(task_id=str(task.id))

    async def test_reopen_nonexistent_task_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """Reopening a non-existent task raises ToolError."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import reopen_task

        with pytest.raises(ToolError, match="Task not found"):
            await reopen_task.fn(task_id="000000000000000000000000")


class TestDeleteComment:
    async def test_delete_existing_comment(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Deleting an existing comment removes it from the task."""
        from app.mcp.tools.tasks import delete_comment

        task = await make_task(str(test_project.id), admin_user)
        comment = Comment(content="To be deleted", author_id="mcp", author_name="Claude")
        task.comments.append(comment)
        await task.save()

        result = await delete_comment.fn(task_id=str(task.id), comment_id=comment.id)

        assert len(result["comments"]) == 0

        db_task = await Task.get(task.id)
        assert len(db_task.comments) == 0

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "comment.deleted"
        assert call_args[0][2]["comment_id"] == comment.id

    async def test_delete_one_of_multiple_comments(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Deleting one comment preserves others."""
        from app.mcp.tools.tasks import delete_comment

        task = await make_task(str(test_project.id), admin_user)
        comment1 = Comment(content="Keep me", author_id="mcp", author_name="Claude")
        comment2 = Comment(content="Delete me", author_id="mcp", author_name="Claude")
        task.comments = [comment1, comment2]
        await task.save()

        result = await delete_comment.fn(task_id=str(task.id), comment_id=comment2.id)

        assert len(result["comments"]) == 1
        assert result["comments"][0]["content"] == "Keep me"

    async def test_delete_nonexistent_comment_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Deleting a non-existent comment raises ToolError."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import delete_comment

        task = await make_task(str(test_project.id), admin_user)

        with pytest.raises(ToolError, match="Comment not found"):
            await delete_comment.fn(task_id=str(task.id), comment_id="nonexistent-id")

    async def test_delete_comment_on_deleted_task_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Deleting a comment on a soft-deleted task raises ToolError."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import delete_comment

        task = await make_task(
            str(test_project.id), admin_user, is_deleted=True
        )

        with pytest.raises(ToolError, match="Task not found"):
            await delete_comment.fn(task_id=str(task.id), comment_id="any-id")

    async def test_delete_comment_on_nonexistent_task_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """Deleting a comment on a non-existent task raises ToolError."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import delete_comment

        with pytest.raises(ToolError, match="Task not found"):
            await delete_comment.fn(
                task_id="000000000000000000000000", comment_id="any-id"
            )


# ---------------------------------------------------------------------------
# get_subtasks
# ---------------------------------------------------------------------------


class TestGetSubtasks:
    async def test_returns_subtasks_of_parent(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        parent = await make_task(pid, admin_user, title="Parent")
        child1 = await make_task(pid, admin_user, title="Child 1", parent_task_id=str(parent.id))
        child2 = await make_task(pid, admin_user, title="Child 2", parent_task_id=str(parent.id))
        # Unrelated task (no parent)
        await make_task(pid, admin_user, title="Unrelated")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_subtasks

            result = await get_subtasks.fn(task_id=str(parent.id))

        assert result["total"] == 2
        titles = {item["title"] for item in result["items"]}
        assert titles == {"Child 1", "Child 2"}

    async def test_excludes_deleted_subtasks(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        parent = await make_task(pid, admin_user, title="Parent")
        await make_task(pid, admin_user, title="Active", parent_task_id=str(parent.id))
        await make_task(pid, admin_user, title="Deleted", parent_task_id=str(parent.id), is_deleted=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_subtasks

            result = await get_subtasks.fn(task_id=str(parent.id))

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active"

    async def test_filter_subtasks_by_status(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        parent = await make_task(pid, admin_user, title="Parent")
        await make_task(pid, admin_user, title="Todo", parent_task_id=str(parent.id), status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Done", parent_task_id=str(parent.id), status=TaskStatus.done)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_subtasks

            result = await get_subtasks.fn(task_id=str(parent.id), status="done")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Done"

    async def test_parent_not_found_raises(
        self, admin_user, test_project,
    ):
        from fastmcp.exceptions import ToolError

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_subtasks

            with pytest.raises(ToolError, match="Task not found"):
                await get_subtasks.fn(task_id="000000000000000000000000")

    async def test_no_subtasks_returns_empty(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        parent = await make_task(pid, admin_user, title="Lonely Parent")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_subtasks

            result = await get_subtasks.fn(task_id=str(parent.id))

        assert result["total"] == 0
        assert result["items"] == []


# ---------------------------------------------------------------------------
# list_tasks date range filters
# ---------------------------------------------------------------------------


class TestListTasksDateFilter:
    async def test_due_before_filter(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        early = datetime(2025, 1, 15, tzinfo=UTC)
        late = datetime(2025, 6, 15, tzinfo=UTC)
        await make_task(pid, admin_user, title="Early", due_date=early)
        await make_task(pid, admin_user, title="Late", due_date=late)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            # Patch _resolve_project_id to pass through
            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(
                    project_id=pid,
                    due_before="2025-03-01T00:00:00+00:00",
                )

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Early"

    async def test_due_after_filter(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        early = datetime(2025, 1, 15, tzinfo=UTC)
        late = datetime(2025, 6, 15, tzinfo=UTC)
        await make_task(pid, admin_user, title="Early", due_date=early)
        await make_task(pid, admin_user, title="Late", due_date=late)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(
                    project_id=pid,
                    due_after="2025-03-01T00:00:00+00:00",
                )

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Late"

    async def test_due_date_range_filter(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        d1 = datetime(2025, 1, 10, tzinfo=UTC)
        d2 = datetime(2025, 3, 15, tzinfo=UTC)
        d3 = datetime(2025, 6, 20, tzinfo=UTC)
        await make_task(pid, admin_user, title="Jan", due_date=d1)
        await make_task(pid, admin_user, title="Mar", due_date=d2)
        await make_task(pid, admin_user, title="Jun", due_date=d3)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(
                    project_id=pid,
                    due_after="2025-02-01T00:00:00+00:00",
                    due_before="2025-05-01T00:00:00+00:00",
                )

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Mar"

    async def test_no_date_filters_returns_all(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        d1 = datetime(2025, 1, 10, tzinfo=UTC)
        await make_task(pid, admin_user, title="With date", due_date=d1)
        await make_task(pid, admin_user, title="No date")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid)

        assert result["total"] == 2


# ---------------------------------------------------------------------------
# list_tags
# ---------------------------------------------------------------------------


class TestListTags:
    async def test_returns_unique_tags(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="T1", tags=["bug", "backend"])
        await make_task(pid, admin_user, title="T2", tags=["bug", "frontend"])
        await make_task(pid, admin_user, title="T3", tags=["feature"])

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tags

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tags.fn(project_id=pid)

        assert result == ["backend", "bug", "feature", "frontend"]

    async def test_returns_empty_for_no_tags(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="T1", tags=[])
        await make_task(pid, admin_user, title="T2")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tags

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tags.fn(project_id=pid)

        assert result == []

    async def test_excludes_deleted_task_tags(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Active", tags=["keep"])
        await make_task(pid, admin_user, title="Deleted", tags=["remove"], is_deleted=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tags

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tags.fn(project_id=pid)

        assert result == ["keep"]

    async def test_returns_sorted_tags(
        self, admin_user, test_project,
    ):
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="T1", tags=["zeta", "alpha", "mu"])

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tags

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tags.fn(project_id=pid)

        assert result == ["alpha", "mu", "zeta"]


# ---------------------------------------------------------------------------
# list_tasks basic filters & pagination
# ---------------------------------------------------------------------------


class TestListTasks:
    async def test_list_all_tasks(self, admin_user, test_project):
        """list_tasks returns all non-deleted tasks."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="T1")
        await make_task(pid, admin_user, title="T2")
        await make_task(pid, admin_user, title="Deleted", is_deleted=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid)

        assert result["total"] == 2
        titles = {item["title"] for item in result["items"]}
        assert titles == {"T1", "T2"}

    async def test_filter_by_status(self, admin_user, test_project):
        """list_tasks filters by status."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Todo", status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Done", status=TaskStatus.done)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid, status="done")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Done"

    async def test_filter_by_priority(self, admin_user, test_project):
        """list_tasks filters by priority."""
        from app.models.task import TaskPriority

        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Low", priority=TaskPriority.low)
        await make_task(pid, admin_user, title="Urgent", priority=TaskPriority.urgent)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid, priority="urgent")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Urgent"

    async def test_filter_by_tag(self, admin_user, test_project):
        """list_tasks filters by tag."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Bug", tags=["bug"])
        await make_task(pid, admin_user, title="Feature", tags=["feature"])

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid, tag="bug")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Bug"

    async def test_filter_by_needs_detail(self, admin_user, test_project):
        """list_tasks filters by needs_detail flag."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Needs detail", needs_detail=True)
        await make_task(pid, admin_user, title="Normal", needs_detail=False)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid, needs_detail=True)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Needs detail"

    async def test_filter_by_approved(self, admin_user, test_project):
        """list_tasks filters by approved flag."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Approved", approved=True)
        await make_task(pid, admin_user, title="Not approved", approved=False)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid, approved=True)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Approved"

    async def test_pagination_limit_skip(self, admin_user, test_project):
        """list_tasks respects limit and skip parameters."""
        pid = str(test_project.id)
        for i in range(5):
            await make_task(pid, admin_user, title=f"Task {i}")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_tasks.fn(project_id=pid, limit=2, skip=1)

        assert result["total"] == 5
        assert len(result["items"]) == 2
        assert result["limit"] == 2
        assert result["skip"] == 1


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


class TestGetTask:
    async def test_get_existing_task(self, admin_user, test_project):
        """get_task returns a single task dict."""
        pid = str(test_project.id)
        task = await make_task(pid, admin_user, title="My Task")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_task

            result = await get_task.fn(task_id=str(task.id))

        assert result["id"] == str(task.id)
        assert result["title"] == "My Task"
        assert result["project_id"] == pid

    async def test_get_nonexistent_task_raises(self):
        """get_task raises ToolError for missing task."""
        from fastmcp.exceptions import ToolError

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_task

            with pytest.raises(ToolError, match="Task not found"):
                await get_task.fn(task_id="000000000000000000000000")

    async def test_get_deleted_task_raises(self, admin_user, test_project):
        """get_task raises ToolError for soft-deleted task."""
        from fastmcp.exceptions import ToolError

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, is_deleted=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import get_task

            with pytest.raises(ToolError, match="Task not found"):
                await get_task.fn(task_id=str(task.id))


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


class TestCreateTask:
    async def test_basic_creation(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """create_task creates a task with defaults."""
        from app.mcp.tools.tasks import create_task

        pid = str(test_project.id)

        with patch(
            "app.mcp.tools.tasks._resolve_project_id",
            new_callable=AsyncMock,
            return_value=pid,
        ):
            result = await create_task.fn(project_id=pid, title="New Task")

        assert result["title"] == "New Task"
        assert result["status"] == "todo"
        assert result["priority"] == "medium"
        assert result["description"] == ""
        assert result["tags"] == []
        assert result["created_by"] == "mcp"
        assert result["project_id"] == pid

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "task.created"

    async def test_creation_with_all_fields(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """create_task accepts all optional fields."""
        from app.mcp.tools.tasks import create_task

        pid = str(test_project.id)

        with patch(
            "app.mcp.tools.tasks._resolve_project_id",
            new_callable=AsyncMock,
            return_value=pid,
        ):
            result = await create_task.fn(
                project_id=pid,
                title="Full Task",
                description="Detailed description",
                priority="high",
                status="in_progress",
                due_date="2025-12-31T00:00:00+00:00",
                assignee_id="user-123",
                tags=["backend", "urgent"],
            )

        assert result["title"] == "Full Task"
        assert result["description"] == "Detailed description"
        assert result["priority"] == "high"
        assert result["status"] == "in_progress"
        assert result["due_date"] is not None
        assert result["assignee_id"] == "user-123"
        assert result["tags"] == ["backend", "urgent"]

    async def test_create_with_parent_task_id(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """create_task can set parent_task_id for subtasks."""
        from app.mcp.tools.tasks import create_task

        pid = str(test_project.id)
        parent = await make_task(pid, admin_user, title="Parent")

        with patch(
            "app.mcp.tools.tasks._resolve_project_id",
            new_callable=AsyncMock,
            return_value=pid,
        ):
            result = await create_task.fn(
                project_id=pid,
                title="Subtask",
                parent_task_id=str(parent.id),
            )

        assert result["parent_task_id"] == str(parent.id)

    async def test_create_persists_to_db(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """create_task persists the task in the database."""
        from app.mcp.tools.tasks import create_task

        pid = str(test_project.id)

        with patch(
            "app.mcp.tools.tasks._resolve_project_id",
            new_callable=AsyncMock,
            return_value=pid,
        ):
            result = await create_task.fn(project_id=pid, title="Persisted")

        db_task = await Task.get(result["id"])
        assert db_task is not None
        assert db_task.title == "Persisted"


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


class TestUpdateTask:
    async def test_update_title(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """update_task changes the title."""
        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, title="Old Title")

        result = await update_task.fn(task_id=str(task.id), title="New Title")

        assert result["title"] == "New Title"

        db_task = await Task.get(task.id)
        assert db_task.title == "New Title"

    async def test_update_multiple_fields(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """update_task changes multiple fields at once."""
        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, title="Task")

        result = await update_task.fn(
            task_id=str(task.id),
            title="Updated",
            description="New desc",
            priority="high",
            tags=["new-tag"],
        )

        assert result["title"] == "Updated"
        assert result["description"] == "New desc"
        assert result["priority"] == "high"
        assert result["tags"] == ["new-tag"]

    async def test_update_status_to_done_sets_completed_at(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Updating status to done sets completed_at."""
        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, status=TaskStatus.todo)

        result = await update_task.fn(task_id=str(task.id), status="done")

        assert result["status"] == "done"
        assert result["completed_at"] is not None

    async def test_update_status_from_done_clears_completed_at(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """Updating status from done clears completed_at."""
        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, status=TaskStatus.done)
        task.completed_at = datetime.now(UTC)
        await task.save()

        result = await update_task.fn(task_id=str(task.id), status="todo")

        assert result["status"] == "todo"
        assert result["completed_at"] is None

    async def test_update_invalid_status_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """update_task raises ToolError for invalid status."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user)

        with pytest.raises(ToolError, match="Invalid status"):
            await update_task.fn(task_id=str(task.id), status="bogus")

    async def test_update_invalid_priority_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """update_task raises ToolError for invalid priority."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user)

        with pytest.raises(ToolError, match="Invalid priority"):
            await update_task.fn(task_id=str(task.id), priority="bogus")

    async def test_update_nonexistent_task_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """update_task raises ToolError for missing task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import update_task

        with pytest.raises(ToolError, match="Task not found"):
            await update_task.fn(task_id="000000000000000000000000", title="X")

    async def test_update_deleted_task_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """update_task raises ToolError for soft-deleted task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, is_deleted=True)

        with pytest.raises(ToolError, match="Task not found"):
            await update_task.fn(task_id=str(task.id), title="X")

    async def test_update_publishes_event(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """update_task publishes a task.updated event."""
        from app.mcp.tools.tasks import update_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user)

        await update_task.fn(task_id=str(task.id), title="Updated")

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "task.updated"


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


class TestDeleteTask:
    async def test_soft_delete(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """delete_task soft-deletes the task."""
        from app.mcp.tools.tasks import delete_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, title="To Delete")

        result = await delete_task.fn(task_id=str(task.id))

        assert result["success"] is True
        assert result["task_id"] == str(task.id)

        db_task = await Task.get(task.id)
        assert db_task.is_deleted is True

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "task.deleted"

    async def test_delete_nonexistent_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """delete_task raises ToolError for missing task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import delete_task

        with pytest.raises(ToolError, match="Task not found"):
            await delete_task.fn(task_id="000000000000000000000000")

    async def test_delete_already_deleted_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """delete_task raises ToolError for already deleted task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import delete_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, is_deleted=True)

        with pytest.raises(ToolError, match="Task not found"):
            await delete_task.fn(task_id=str(task.id))


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------


class TestCompleteTask:
    async def test_marks_done_with_completed_at(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """complete_task sets status to done and completed_at."""
        from app.mcp.tools.tasks import complete_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, status=TaskStatus.todo)

        result = await complete_task.fn(task_id=str(task.id))

        assert result["status"] == "done"
        assert result["completed_at"] is not None

        db_task = await Task.get(task.id)
        assert db_task.status == TaskStatus.done
        assert db_task.completed_at is not None

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "task.updated"

    async def test_already_done_is_idempotent(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """complete_task on already-done task is idempotent (no extra save/event)."""
        from app.mcp.tools.tasks import complete_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, status=TaskStatus.done)
        task.completed_at = datetime.now(UTC)
        await task.save()

        result = await complete_task.fn(task_id=str(task.id))

        assert result["status"] == "done"
        # publish_event should NOT be called because status was already done
        mock_publish.assert_not_called()

    async def test_complete_nonexistent_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """complete_task raises ToolError for missing task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import complete_task

        with pytest.raises(ToolError, match="Task not found"):
            await complete_task.fn(task_id="000000000000000000000000")

    async def test_complete_deleted_task_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """complete_task raises ToolError for soft-deleted task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import complete_task

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, is_deleted=True)

        with pytest.raises(ToolError, match="Task not found"):
            await complete_task.fn(task_id=str(task.id))


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


class TestAddComment:
    async def test_adds_comment(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """add_comment appends a comment to the task."""
        from app.mcp.tools.tasks import add_comment

        pid = str(test_project.id)
        task = await make_task(pid, admin_user)

        result = await add_comment.fn(task_id=str(task.id), content="Hello")

        assert len(result["comments"]) == 1
        assert result["comments"][0]["content"] == "Hello"
        assert result["comments"][0]["author_id"] == "mcp"
        assert result["comments"][0]["author_name"] == "Claude"

        db_task = await Task.get(task.id)
        assert len(db_task.comments) == 1
        assert db_task.comments[0].content == "Hello"

        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "comment.added"

    async def test_add_comment_to_nonexistent_task_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """add_comment raises ToolError for missing task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import add_comment

        with pytest.raises(ToolError, match="Task not found"):
            await add_comment.fn(
                task_id="000000000000000000000000", content="Hello"
            )

    async def test_add_comment_to_deleted_task_raises(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """add_comment raises ToolError for soft-deleted task."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import add_comment

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, is_deleted=True)

        with pytest.raises(ToolError, match="Task not found"):
            await add_comment.fn(task_id=str(task.id), content="Hello")

    async def test_add_multiple_comments(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """add_comment appends to existing comments."""
        from app.mcp.tools.tasks import add_comment

        pid = str(test_project.id)
        task = await make_task(pid, admin_user)
        comment = Comment(content="First", author_id="mcp", author_name="Claude")
        task.comments.append(comment)
        await task.save()

        result = await add_comment.fn(task_id=str(task.id), content="Second")

        assert len(result["comments"]) == 2
        assert result["comments"][0]["content"] == "First"
        assert result["comments"][1]["content"] == "Second"


# ---------------------------------------------------------------------------
# search_tasks
# ---------------------------------------------------------------------------


class TestSearchTasks:
    async def test_search_by_title(self, admin_user, test_project):
        """search_tasks finds tasks matching title."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Login Bug")
        await make_task(pid, admin_user, title="Signup Feature")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            result = await search_tasks.fn(query="Login")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Login Bug"

    async def test_search_by_description(self, admin_user, test_project):
        """search_tasks finds tasks matching description."""
        pid = str(test_project.id)
        task = await make_task(pid, admin_user, title="Task A")
        task.description = "Fix the authentication flow"
        await task.save()
        await make_task(pid, admin_user, title="Task B")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            result = await search_tasks.fn(query="authentication")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Task A"

    async def test_search_case_insensitive(self, admin_user, test_project):
        """search_tasks is case-insensitive."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="UPPERCASE BUG")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            result = await search_tasks.fn(query="uppercase")

        assert result["total"] == 1

    async def test_search_excludes_deleted(self, admin_user, test_project):
        """search_tasks excludes soft-deleted tasks."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Active Bug")
        await make_task(pid, admin_user, title="Deleted Bug", is_deleted=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            result = await search_tasks.fn(query="Bug")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active Bug"

    async def test_search_with_project_filter(self, admin_user, test_project):
        """search_tasks can be scoped to a specific project."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Scoped Bug")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await search_tasks.fn(query="Scoped", project_id=pid)

        assert result["total"] == 1

    async def test_search_with_status_filter(self, admin_user, test_project):
        """search_tasks filters by status."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Bug Todo", status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Bug Done", status=TaskStatus.done)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            result = await search_tasks.fn(query="Bug", status="todo")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Bug Todo"

    async def test_search_no_results(self, admin_user, test_project):
        """search_tasks returns empty when no match."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Hello World")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            result = await search_tasks.fn(query="nonexistent_xyz")

        assert result["total"] == 0
        assert result["items"] == []


# ---------------------------------------------------------------------------
# list_overdue_tasks
# ---------------------------------------------------------------------------


class TestListOverdueTasks:
    async def test_returns_overdue_tasks(self, admin_user, test_project):
        """list_overdue_tasks returns tasks past due date."""
        pid = str(test_project.id)
        past = datetime(2020, 1, 1, tzinfo=UTC)
        future = datetime(2099, 1, 1, tzinfo=UTC)
        await make_task(pid, admin_user, title="Overdue", due_date=past)
        await make_task(pid, admin_user, title="Future", due_date=future)
        await make_task(pid, admin_user, title="No date")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_overdue_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_overdue_tasks.fn(project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Overdue"

    async def test_excludes_done_tasks(self, admin_user, test_project):
        """list_overdue_tasks excludes done tasks."""
        pid = str(test_project.id)
        past = datetime(2020, 1, 1, tzinfo=UTC)
        await make_task(pid, admin_user, title="Overdue Todo", due_date=past, status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Overdue Done", due_date=past, status=TaskStatus.done)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_overdue_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_overdue_tasks.fn(project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Overdue Todo"

    async def test_excludes_cancelled_tasks(self, admin_user, test_project):
        """list_overdue_tasks excludes cancelled tasks."""
        pid = str(test_project.id)
        past = datetime(2020, 1, 1, tzinfo=UTC)
        await make_task(pid, admin_user, title="Overdue Active", due_date=past, status=TaskStatus.in_progress)
        await make_task(pid, admin_user, title="Overdue Cancelled", due_date=past, status=TaskStatus.cancelled)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_overdue_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_overdue_tasks.fn(project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Overdue Active"

    async def test_excludes_deleted_tasks(self, admin_user, test_project):
        """list_overdue_tasks excludes soft-deleted tasks."""
        pid = str(test_project.id)
        past = datetime(2020, 1, 1, tzinfo=UTC)
        await make_task(pid, admin_user, title="Active Overdue", due_date=past)
        await make_task(pid, admin_user, title="Deleted Overdue", due_date=past, is_deleted=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_overdue_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_overdue_tasks.fn(project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active Overdue"

    async def test_no_overdue_returns_empty(self, admin_user, test_project):
        """list_overdue_tasks returns empty when no tasks are overdue."""
        pid = str(test_project.id)
        future = datetime(2099, 1, 1, tzinfo=UTC)
        await make_task(pid, admin_user, title="Future", due_date=future)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_overdue_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_overdue_tasks.fn(project_id=pid)

        assert result["total"] == 0
        assert result["items"] == []


# ---------------------------------------------------------------------------
# batch_create_tasks
# ---------------------------------------------------------------------------


class TestBatchCreateTasks:
    async def test_creates_multiple_tasks(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_create_tasks creates multiple tasks."""
        from app.mcp.tools.tasks import batch_create_tasks

        pid = str(test_project.id)

        with patch(
            "app.mcp.tools.tasks._resolve_project_id",
            new_callable=AsyncMock,
            return_value=pid,
        ):
            result = await batch_create_tasks.fn(
                project_id=pid,
                tasks=[
                    {"title": "Task A"},
                    {"title": "Task B", "priority": "high"},
                    {"title": "Task C", "tags": ["backend"]},
                ],
            )

        assert len(result["created"]) == 3
        assert len(result["failed"]) == 0

        titles = {t["title"] for t in result["created"]}
        assert titles == {"Task A", "Task B", "Task C"}

        # Verify task B has high priority
        task_b = next(t for t in result["created"] if t["title"] == "Task B")
        assert task_b["priority"] == "high"

        # publish_event called once for the batch
        assert mock_publish.call_count == 1

    async def test_handles_failures(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_create_tasks reports failures for invalid items."""
        from app.mcp.tools.tasks import batch_create_tasks

        pid = str(test_project.id)

        with patch(
            "app.mcp.tools.tasks._resolve_project_id",
            new_callable=AsyncMock,
            return_value=pid,
        ):
            result = await batch_create_tasks.fn(
                project_id=pid,
                tasks=[
                    {"title": "Valid Task"},
                    {"title": "Invalid", "priority": "not_a_priority"},
                ],
            )

        assert len(result["created"]) == 1
        assert result["created"][0]["title"] == "Valid Task"
        assert len(result["failed"]) == 1
        assert result["failed"][0]["title"] == "Invalid"

    async def test_persists_to_db(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_create_tasks persists tasks in the database."""
        from app.mcp.tools.tasks import batch_create_tasks

        pid = str(test_project.id)

        with patch(
            "app.mcp.tools.tasks._resolve_project_id",
            new_callable=AsyncMock,
            return_value=pid,
        ):
            result = await batch_create_tasks.fn(
                project_id=pid,
                tasks=[{"title": "Persisted A"}, {"title": "Persisted B"}],
            )

        db_tasks = await Task.find(
            Task.project_id == pid, Task.is_deleted == False  # noqa: E712
        ).to_list()
        db_titles = {t.title for t in db_tasks}
        assert "Persisted A" in db_titles
        assert "Persisted B" in db_titles


# ---------------------------------------------------------------------------
# batch_update_tasks
# ---------------------------------------------------------------------------


class TestBatchUpdateTasks:
    async def test_updates_multiple_tasks(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_update_tasks updates multiple tasks."""
        from app.mcp.tools.tasks import batch_update_tasks

        pid = str(test_project.id)
        task1 = await make_task(pid, admin_user, title="Task 1")
        task2 = await make_task(pid, admin_user, title="Task 2")

        result = await batch_update_tasks.fn(
            updates=[
                {"task_id": str(task1.id), "title": "Updated 1"},
                {"task_id": str(task2.id), "priority": "high"},
            ]
        )

        assert len(result["updated"]) == 2
        assert len(result["failed"]) == 0

        updated_1 = next(t for t in result["updated"] if t["id"] == str(task1.id))
        assert updated_1["title"] == "Updated 1"

        updated_2 = next(t for t in result["updated"] if t["id"] == str(task2.id))
        assert updated_2["priority"] == "high"

    async def test_handles_missing_task_id(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_update_tasks reports failure when task_id is missing."""
        from app.mcp.tools.tasks import batch_update_tasks

        result = await batch_update_tasks.fn(
            updates=[{"title": "No ID"}]
        )

        assert len(result["updated"]) == 0
        assert len(result["failed"]) == 1
        assert result["failed"][0]["error"] == "task_id required"

    async def test_handles_not_found_task(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_update_tasks reports failure for non-existent task."""
        from app.mcp.tools.tasks import batch_update_tasks

        result = await batch_update_tasks.fn(
            updates=[{"task_id": "000000000000000000000000", "title": "X"}]
        )

        assert len(result["updated"]) == 0
        assert len(result["failed"]) == 1
        assert result["failed"][0]["error"] == "Task not found"

    async def test_handles_deleted_task(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_update_tasks reports failure for soft-deleted task."""
        from app.mcp.tools.tasks import batch_update_tasks

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, is_deleted=True)

        result = await batch_update_tasks.fn(
            updates=[{"task_id": str(task.id), "title": "X"}]
        )

        assert len(result["updated"]) == 0
        assert len(result["failed"]) == 1
        assert result["failed"][0]["error"] == "Task not found"

    async def test_mixed_success_and_failure(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_update_tasks handles mix of valid and invalid updates."""
        from app.mcp.tools.tasks import batch_update_tasks

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, title="Valid")

        result = await batch_update_tasks.fn(
            updates=[
                {"task_id": str(task.id), "title": "Updated"},
                {"task_id": "000000000000000000000000", "title": "Missing"},
            ]
        )

        assert len(result["updated"]) == 1
        assert result["updated"][0]["title"] == "Updated"
        assert len(result["failed"]) == 1

    async def test_status_to_done_sets_completed_at(
        self, admin_user, test_project, mock_auth, mock_check, mock_publish
    ):
        """batch_update_tasks sets completed_at when status changes to done."""
        from app.mcp.tools.tasks import batch_update_tasks

        pid = str(test_project.id)
        task = await make_task(pid, admin_user, status=TaskStatus.todo)

        result = await batch_update_tasks.fn(
            updates=[{"task_id": str(task.id), "status": "done"}]
        )

        assert len(result["updated"]) == 1
        assert result["updated"][0]["completed_at"] is not None


# ---------------------------------------------------------------------------
# list_review_tasks
# ---------------------------------------------------------------------------


class TestListReviewTasks:
    async def test_filter_needs_detail(self, admin_user, test_project):
        """list_review_tasks filters by needs_detail flag."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Needs Detail", needs_detail=True)
        await make_task(pid, admin_user, title="Normal")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_review_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_review_tasks.fn(project_id=pid, flag="needs_detail")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Needs Detail"

    async def test_filter_approved(self, admin_user, test_project):
        """list_review_tasks filters by approved flag."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Approved", approved=True)
        await make_task(pid, admin_user, title="Not Approved")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_review_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_review_tasks.fn(project_id=pid, flag="approved")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Approved"

    async def test_filter_pending(self, admin_user, test_project):
        """list_review_tasks filters by pending (neither flag set)."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Pending", needs_detail=False, approved=False)
        await make_task(pid, admin_user, title="Needs Detail", needs_detail=True)
        await make_task(pid, admin_user, title="Approved", approved=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_review_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_review_tasks.fn(project_id=pid, flag="pending")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Pending"

    async def test_filter_all(self, admin_user, test_project):
        """list_review_tasks returns all tasks with flag='all'."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="T1", needs_detail=True)
        await make_task(pid, admin_user, title="T2", approved=True)
        await make_task(pid, admin_user, title="T3")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_review_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_review_tasks.fn(project_id=pid, flag="all")

        assert result["total"] == 3

    async def test_invalid_flag_raises(self, admin_user, test_project):
        """list_review_tasks raises ToolError for invalid flag."""
        from fastmcp.exceptions import ToolError

        pid = str(test_project.id)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_review_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                with pytest.raises(ToolError, match="Invalid flag"):
                    await list_review_tasks.fn(project_id=pid, flag="bogus")

    async def test_excludes_deleted_tasks(self, admin_user, test_project):
        """list_review_tasks excludes soft-deleted tasks."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Active", needs_detail=True)
        await make_task(pid, admin_user, title="Deleted", needs_detail=True, is_deleted=True)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_review_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await list_review_tasks.fn(project_id=pid, flag="needs_detail")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active"


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------


class TestListUsers:
    async def test_returns_active_users(self, admin_user, regular_user):
        """list_users returns all active users."""
        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_users

            result = await list_users.fn()

        assert len(result) == 2
        emails = {u["email"] for u in result}
        assert "admin@test.com" in emails
        assert "user@test.com" in emails
        # Verify structure
        for u in result:
            assert "id" in u
            assert "name" in u
            assert "email" in u

    async def test_excludes_inactive_users(self, admin_user, inactive_user):
        """list_users excludes inactive users."""
        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_users

            result = await list_users.fn()

        assert len(result) == 1
        assert result[0]["email"] == "admin@test.com"

    async def test_returns_empty_when_no_active_users(self):
        """list_users returns empty list when no active users exist."""
        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import list_users

            result = await list_users.fn()

        assert result == []


# ---------------------------------------------------------------------------
# projects.py tools helpers
# ---------------------------------------------------------------------------


def _patch_project_auth():
    """Patch authenticate() and check_project_access() for projects.py tools."""
    return [
        patch(
            "app.mcp.tools.projects.authenticate",
            new_callable=AsyncMock,
            return_value=_MOCK_KEY_INFO,
        ),
        patch("app.mcp.tools.projects.check_project_access"),
    ]


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


class TestListProjects:
    async def test_returns_active_projects(self, admin_user, test_project):
        """list_projects returns active projects."""
        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import list_projects

            result = await list_projects.fn()

        assert len(result) == 1
        assert result[0]["name"] == "Test Project"
        assert result[0]["id"] == str(test_project.id)

    async def test_excludes_archived_projects(self, admin_user, test_project):
        """list_projects excludes archived projects."""
        from app.models.project import ProjectStatus
        from tests.helpers.factories import make_project

        project2 = await make_project(admin_user, name="Archived Project")
        project2.status = ProjectStatus.archived
        await project2.save()

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import list_projects

            result = await list_projects.fn()

        names = {p["name"] for p in result}
        assert "Test Project" in names
        assert "Archived Project" not in names

    async def test_respects_project_scopes(self, admin_user, test_project):
        """list_projects filters by scoped project IDs when scopes set."""
        from bson import ObjectId as BsonObjectId

        from tests.helpers.factories import make_project

        project2 = await make_project(admin_user, name="Scoped Out")

        # mongomock stores _id as ObjectId, so scopes must also use ObjectId
        scoped_key_info = {
            "key_id": "test-key",
            "project_scopes": [BsonObjectId(str(test_project.id))],
        }
        patches = [
            patch(
                "app.mcp.tools.projects.authenticate",
                new_callable=AsyncMock,
                return_value=scoped_key_info,
            ),
            patch("app.mcp.tools.projects.check_project_access"),
        ]
        with patches[0], patches[1]:
            from app.mcp.tools.projects import list_projects

            result = await list_projects.fn()

        names = {p["name"] for p in result}
        assert "Test Project" in names
        assert "Scoped Out" not in names

    async def test_returns_project_dict_structure(self, admin_user, test_project):
        """list_projects returns properly structured project dicts."""
        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import list_projects

            result = await list_projects.fn()

        assert len(result) == 1
        p = result[0]
        assert "id" in p
        assert "name" in p
        assert "description" in p
        assert "color" in p
        assert "status" in p
        assert "members" in p
        assert "created_by" in p
        assert "created_at" in p
        assert "updated_at" in p


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


class TestGetProject:
    async def test_get_existing_project(self, admin_user, test_project):
        """get_project returns a project dict by ID."""
        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await get_project.fn(project_id=pid)

        assert result["id"] == pid
        assert result["name"] == "Test Project"

    async def test_get_nonexistent_project_raises(self):
        """get_project raises ToolError for missing project."""
        from fastmcp.exceptions import ToolError

        fake_id = "000000000000000000000000"

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=fake_id,
            ):
                with pytest.raises(ToolError, match="Project not found"):
                    await get_project.fn(project_id=fake_id)


# ---------------------------------------------------------------------------
# get_project_summary
# ---------------------------------------------------------------------------


class TestGetProjectSummary:
    async def test_task_counts_by_status(self, admin_user, test_project):
        """get_project_summary returns task counts by status."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Todo 1", status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Todo 2", status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Done 1", status=TaskStatus.done)
        await make_task(pid, admin_user, title="InProgress", status=TaskStatus.in_progress)

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project_summary

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await get_project_summary.fn(project_id=pid)

        assert result["total"] == 4
        assert result["by_status"]["todo"] == 2
        assert result["by_status"]["done"] == 1
        assert result["by_status"]["in_progress"] == 1

    async def test_completion_rate(self, admin_user, test_project):
        """get_project_summary calculates correct completion rate."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Done 1", status=TaskStatus.done)
        await make_task(pid, admin_user, title="Done 2", status=TaskStatus.done)
        await make_task(pid, admin_user, title="Todo 1", status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Todo 2", status=TaskStatus.todo)

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project_summary

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await get_project_summary.fn(project_id=pid)

        assert result["completion_rate"] == 50.0

    async def test_empty_project_zero_rate(self, admin_user, test_project):
        """get_project_summary returns 0 completion rate for empty project."""
        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project_summary

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await get_project_summary.fn(project_id=pid)

        assert result["total"] == 0
        assert result["completion_rate"] == 0

    async def test_excludes_deleted_tasks(self, admin_user, test_project):
        """get_project_summary excludes soft-deleted tasks from counts."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="Active", status=TaskStatus.todo)
        await make_task(pid, admin_user, title="Deleted", status=TaskStatus.todo, is_deleted=True)

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project_summary

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await get_project_summary.fn(project_id=pid)

        assert result["total"] == 1

    async def test_nonexistent_project_raises(self):
        """get_project_summary raises ToolError for missing project."""
        from fastmcp.exceptions import ToolError

        fake_id = "000000000000000000000000"

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project_summary

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=fake_id,
            ):
                with pytest.raises(ToolError, match="Project not found"):
                    await get_project_summary.fn(project_id=fake_id)

    async def test_returns_project_id(self, admin_user, test_project):
        """get_project_summary includes project_id in result."""
        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.projects import get_project_summary

            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await get_project_summary.fn(project_id=pid)

        assert result["project_id"] == pid


# ---------------------------------------------------------------------------
# Security: batch_create_tasks ignores caller's created_by
# ---------------------------------------------------------------------------


class TestBatchCreateTasksCreatedBy:
    async def test_ignores_caller_created_by(self, admin_user, test_project):
        """batch_create_tasks must hardcode created_by='mcp', ignoring caller input."""
        pid = str(test_project.id)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import batch_create_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ), patch(
                "app.mcp.tools.tasks.publish_event",
                new_callable=AsyncMock,
            ):
                result = await batch_create_tasks.fn(
                    project_id=pid,
                    tasks=[{"title": "Injected", "created_by": "attacker"}],
                )

        assert len(result["created"]) == 1
        assert result["created"][0]["created_by"] == "mcp"

        # Verify in DB - find by title since insert_many may not set id in mock
        db_task = await Task.find_one(Task.title == "Injected")
        assert db_task is not None
        assert db_task.created_by == "mcp"


# ---------------------------------------------------------------------------
# Security: update_task / batch_update_tasks reject disallowed fields
# ---------------------------------------------------------------------------


class TestUpdateTaskFieldAllowlist:
    async def test_batch_update_rejects_is_deleted(
        self, admin_user, test_project,
    ):
        """batch_update_tasks rejects is_deleted field and reports in failed."""
        task = await make_task(str(test_project.id), admin_user)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import batch_update_tasks

            with patch(
                "app.mcp.tools.tasks.publish_event",
                new_callable=AsyncMock,
            ):
                result = await batch_update_tasks.fn(
                    updates=[{"task_id": str(task.id), "is_deleted": True}],
                )

        assert len(result["failed"]) == 1
        assert "Cannot update field" in result["failed"][0]["error"]
        assert "is_deleted" in result["failed"][0]["error"]

        # Verify task was NOT deleted
        db_task = await Task.get(task.id)
        assert db_task.is_deleted is False

    async def test_batch_update_rejects_created_by(
        self, admin_user, test_project,
    ):
        """batch_update_tasks rejects created_by field."""
        task = await make_task(str(test_project.id), admin_user)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import batch_update_tasks

            with patch(
                "app.mcp.tools.tasks.publish_event",
                new_callable=AsyncMock,
            ):
                result = await batch_update_tasks.fn(
                    updates=[{"task_id": str(task.id), "created_by": "attacker"}],
                )

        assert len(result["failed"]) == 1
        assert "Cannot update field" in result["failed"][0]["error"]

    async def test_batch_update_rejects_comments(
        self, admin_user, test_project,
    ):
        """batch_update_tasks rejects comments field."""
        task = await make_task(str(test_project.id), admin_user)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import batch_update_tasks

            with patch(
                "app.mcp.tools.tasks.publish_event",
                new_callable=AsyncMock,
            ):
                result = await batch_update_tasks.fn(
                    updates=[{"task_id": str(task.id), "comments": []}],
                )

        assert len(result["failed"]) == 1
        assert "Cannot update field" in result["failed"][0]["error"]

    async def test_batch_update_allows_valid_fields(
        self, admin_user, test_project,
    ):
        """batch_update_tasks allows valid fields like title, tags, sort_order."""
        task = await make_task(str(test_project.id), admin_user)

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import batch_update_tasks

            with patch(
                "app.mcp.tools.tasks.publish_event",
                new_callable=AsyncMock,
            ):
                result = await batch_update_tasks.fn(
                    updates=[{
                        "task_id": str(task.id),
                        "title": "Updated Title",
                        "tags": ["security"],
                        "sort_order": 5,
                    }],
                )

        assert len(result["updated"]) == 1
        assert result["updated"][0]["title"] == "Updated Title"
        assert result["updated"][0]["tags"] == ["security"]
        assert result["updated"][0]["sort_order"] == 5


# ---------------------------------------------------------------------------
# Security: search_tasks regex injection prevention
# ---------------------------------------------------------------------------


class TestSearchTasksRegexEscape:
    async def test_search_escapes_regex_special_chars(
        self, admin_user, test_project,
    ):
        """search_tasks escapes regex special characters to prevent injection."""
        pid = str(test_project.id)
        # Create a task with literal regex chars in title
        await make_task(pid, admin_user, title="Fix bug (critical)")
        await make_task(pid, admin_user, title="Fix bug critical")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                # Search with parentheses - should be treated as literal, not regex group
                result = await search_tasks.fn(query="(critical)", project_id=pid)

        # Only the task with literal "(critical)" should match
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Fix bug (critical)"

    async def test_search_with_dot_is_literal(
        self, admin_user, test_project,
    ):
        """search_tasks treats dots as literal characters, not regex wildcards."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="version 2.0 release")
        await make_task(pid, admin_user, title="version 2X0 release")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await search_tasks.fn(query="2.0", project_id=pid)

        # Without escaping, "2.0" would match "2X0" too (dot = any char)
        assert result["total"] == 1
        assert result["items"][0]["title"] == "version 2.0 release"

    async def test_search_with_regex_quantifier(
        self, admin_user, test_project,
    ):
        """search_tasks treats regex quantifiers as literal characters."""
        pid = str(test_project.id)
        await make_task(pid, admin_user, title="task*important")
        await make_task(pid, admin_user, title="taskimportant")

        patches = _patch_mcp_auth()
        with patches[0], patches[1]:
            from app.mcp.tools.tasks import search_tasks

            with patch(
                "app.mcp.tools.tasks._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await search_tasks.fn(query="task*important", project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "task*important"
