"""MCP tool unit tests for reopen_task and delete_comment."""

from datetime import UTC, datetime
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

        result = await reopen_task(task_id=str(task.id))

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

        result = await reopen_task(task_id=str(task.id))

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

        result = await reopen_task(task_id=str(task.id))

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
            await reopen_task(task_id=str(task.id))

    async def test_reopen_nonexistent_task_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """Reopening a non-existent task raises ToolError."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import reopen_task

        with pytest.raises(ToolError, match="Task not found"):
            await reopen_task(task_id="000000000000000000000000")


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

        result = await delete_comment(task_id=str(task.id), comment_id=comment.id)

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

        result = await delete_comment(task_id=str(task.id), comment_id=comment2.id)

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
            await delete_comment(task_id=str(task.id), comment_id="nonexistent-id")

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
            await delete_comment(task_id=str(task.id), comment_id="any-id")

    async def test_delete_comment_on_nonexistent_task_raises(
        self, mock_auth, mock_check, mock_publish
    ):
        """Deleting a comment on a non-existent task raises ToolError."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.tasks import delete_comment

        with pytest.raises(ToolError, match="Task not found"):
            await delete_comment(
                task_id="000000000000000000000000", comment_id="any-id"
            )
