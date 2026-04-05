"""Unit tests for the list_overdue_tasks MCP tool.

Tests verify DB-level filtering: only tasks that are overdue (due_date < now),
not completed/cancelled, and not deleted are returned. Pagination and the
consistent response format are also verified.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models.task import TaskStatus
from tests.helpers.factories import make_project, make_task, make_admin_user


# Patch authenticate + check_project_access for all tests in this module.
# authenticate returns empty scopes (= full access to all projects).
_AUTH_PATCH = patch(
    "app.mcp.tools.tasks.authenticate",
    new_callable=AsyncMock,
    return_value={"key_id": "test-key", "project_scopes": []},
)
_ACCESS_PATCH = patch("app.mcp.tools.tasks.check_project_access")


@pytest.fixture(autouse=True)
def _mock_auth():
    with _AUTH_PATCH, _ACCESS_PATCH:
        yield


@pytest.fixture
async def setup_data():
    """Create a project and an admin user for tests."""
    user = await make_admin_user(email="mcp-test@test.com")
    project = await make_project(user, name="Overdue Test Project")
    return user, project


class TestListOverdueTasks:
    async def test_returns_only_overdue_tasks(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)
        tomorrow = datetime.now(UTC) + timedelta(days=1)

        # overdue: due yesterday, status todo
        await make_task(pid, user, title="Overdue", due_date=yesterday, status=TaskStatus.todo)
        # not overdue: due tomorrow
        await make_task(pid, user, title="Future", due_date=tomorrow, status=TaskStatus.todo)
        # no due date
        await make_task(pid, user, title="No Due", status=TaskStatus.todo)

        result = await list_overdue_tasks(project_id=pid)

        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "Overdue"

    async def test_excludes_done_tasks(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)

        await make_task(pid, user, title="Done Overdue", due_date=yesterday, status=TaskStatus.done)
        await make_task(pid, user, title="Active Overdue", due_date=yesterday, status=TaskStatus.todo)

        result = await list_overdue_tasks(project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active Overdue"

    async def test_excludes_cancelled_tasks(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)

        await make_task(pid, user, title="Cancelled", due_date=yesterday, status=TaskStatus.cancelled)
        await make_task(pid, user, title="In Progress", due_date=yesterday, status=TaskStatus.in_progress)

        result = await list_overdue_tasks(project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "In Progress"

    async def test_excludes_deleted_tasks(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)

        await make_task(pid, user, title="Deleted", due_date=yesterday, is_deleted=True)
        await make_task(pid, user, title="Active", due_date=yesterday, status=TaskStatus.todo)

        result = await list_overdue_tasks(project_id=pid)

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active"

    async def test_sorted_by_due_date_ascending(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        three_days_ago = datetime.now(UTC) - timedelta(days=3)
        one_day_ago = datetime.now(UTC) - timedelta(days=1)

        await make_task(pid, user, title="Recent", due_date=one_day_ago)
        await make_task(pid, user, title="Oldest", due_date=three_days_ago)

        result = await list_overdue_tasks(project_id=pid)

        assert len(result["items"]) == 2
        assert result["items"][0]["title"] == "Oldest"
        assert result["items"][1]["title"] == "Recent"

    async def test_pagination_limit(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)

        for i in range(5):
            await make_task(pid, user, title=f"Task {i}", due_date=yesterday)

        result = await list_overdue_tasks(project_id=pid, limit=3)

        assert result["total"] == 5
        assert len(result["items"]) == 3
        assert result["limit"] == 3
        assert result["skip"] == 0

    async def test_pagination_skip(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)

        for i in range(5):
            await make_task(pid, user, title=f"Task {i}", due_date=yesterday)

        result = await list_overdue_tasks(project_id=pid, limit=2, skip=3)

        assert result["total"] == 5
        assert len(result["items"]) == 2
        assert result["skip"] == 3

    async def test_response_format(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)

        await make_task(pid, user, title="Check Format", due_date=yesterday)

        result = await list_overdue_tasks(project_id=pid)

        assert "items" in result
        assert "total" in result
        assert "limit" in result
        assert "skip" in result
        assert isinstance(result["items"], list)
        assert isinstance(result["total"], int)

    async def test_empty_result(self, setup_data):
        from app.mcp.tools.tasks import list_overdue_tasks

        _, project = setup_data
        pid = str(project.id)

        result = await list_overdue_tasks(project_id=pid)

        assert result["total"] == 0
        assert result["items"] == []

    async def test_cross_project_without_filter(self, setup_data):
        """Without project_id, tasks from all projects are returned."""
        from app.mcp.tools.tasks import list_overdue_tasks

        user, project = setup_data
        pid = str(project.id)
        yesterday = datetime.now(UTC) - timedelta(days=1)

        project2 = await make_project(user, name="Second Project")
        pid2 = str(project2.id)

        await make_task(pid, user, title="P1 Overdue", due_date=yesterday)
        await make_task(pid2, user, title="P2 Overdue", due_date=yesterday)

        result = await list_overdue_tasks()

        assert result["total"] == 2

