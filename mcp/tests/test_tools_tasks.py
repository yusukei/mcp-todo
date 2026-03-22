"""Tests for app.tools.tasks -- all task-related MCP tools."""

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError

from app.auth import McpAuthError
from app.tools.tasks import (
    add_comment,
    batch_create_tasks,
    batch_update_tasks,
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_overdue_tasks,
    list_tasks,
    list_users,
    search_tasks,
    update_task,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _auth_mock(scopes: list[str] | None = None):
    """Return a patch context for authenticate in the tasks module."""
    return patch(
        "app.tools.tasks.authenticate",
        new_callable=AsyncMock,
        return_value={"key_id": "test-key", "project_scopes": scopes or []},
    )


def _br_mock(**kwargs):
    """Return a patch context for backend_request in the tasks module."""
    return patch(
        "app.tools.tasks.backend_request",
        new_callable=AsyncMock,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------

class TestListTasks:

    async def test_basic_list(self):
        """list_tasks passes project_id and returns paginated response."""
        paginated = {"items": [{"id": "t1", "title": "Task 1"}], "total": 1, "limit": 50, "skip": 0}
        with _auth_mock(), _br_mock(return_value=paginated) as mock_br:
            result = await list_tasks(project_id="proj-1")

        assert result == paginated
        mock_br.assert_awaited_once_with("GET", "/projects/proj-1/tasks", params={"limit": 50, "skip": 0})

    async def test_with_status_filter(self):
        """Status filter is forwarded as 'task_status' param key (backend convention)."""
        paginated = {"items": [], "total": 0, "limit": 50, "skip": 0}
        with _auth_mock(), _br_mock(return_value=paginated) as mock_br:
            await list_tasks(project_id="proj-1", status="todo")

        call_params = mock_br.call_args.kwargs["params"]
        assert call_params["task_status"] == "todo"
        assert call_params["limit"] == 50
        assert call_params["skip"] == 0

    async def test_with_multiple_filters(self):
        """Multiple filters are all forwarded as params."""
        paginated = {"items": [], "total": 0, "limit": 50, "skip": 0}
        with _auth_mock(), _br_mock(return_value=paginated) as mock_br:
            await list_tasks(project_id="proj-1", status="in_progress", priority="high", tag="bug")

        call_params = mock_br.call_args.kwargs["params"]
        assert call_params == {"task_status": "in_progress", "priority": "high", "tag": "bug", "limit": 50, "skip": 0}

    async def test_with_pagination(self):
        """Pagination params are forwarded."""
        paginated = {"items": [], "total": 100, "limit": 10, "skip": 20}
        with _auth_mock(), _br_mock(return_value=paginated) as mock_br:
            result = await list_tasks(project_id="proj-1", limit=10, skip=20)

        assert result["total"] == 100
        call_params = mock_br.call_args.kwargs["params"]
        assert call_params["limit"] == 10
        assert call_params["skip"] == 20

    async def test_scope_check_enforced(self):
        """list_tasks raises McpAuthError when project is out of scope."""
        with _auth_mock(scopes=["proj-other"]):
            with pytest.raises(McpAuthError, match="No access to project proj-1"):
                await list_tasks(project_id="proj-1")


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------

class TestGetTask:

    async def test_returns_task_dict(self):
        """get_task fetches and returns the task."""
        task = {"id": "t1", "project_id": "proj-1", "title": "Task"}
        with _auth_mock(), _br_mock(return_value=task) as mock_br:
            result = await get_task(task_id="t1")

        assert result == task
        mock_br.assert_awaited_once_with("GET", "/tasks/t1")

    async def test_scope_check_after_fetch(self):
        """get_task fetches the task first then checks project scope."""
        task = {"id": "t1", "project_id": "proj-99", "title": "Task"}
        with _auth_mock(scopes=["proj-1"]), _br_mock(return_value=task):
            with pytest.raises(McpAuthError, match="No access to project proj-99"):
                await get_task(task_id="t1")


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

class TestCreateTask:

    async def test_minimal_create(self):
        """create_task sends correct body with required fields."""
        created = {"id": "t-new", "title": "New task"}
        with _auth_mock(), _br_mock(return_value=created) as mock_br:
            result = await create_task(project_id="proj-1", title="New task")

        assert result == created
        call_json = mock_br.call_args.kwargs["json"]
        assert call_json["title"] == "New task"
        assert call_json["created_by"] == "mcp"
        assert call_json["priority"] == "medium"
        assert call_json["status"] == "todo"

    async def test_full_create(self):
        """create_task includes all optional fields when provided."""
        with _auth_mock(), _br_mock(return_value={"id": "t-new"}) as mock_br:
            await create_task(
                project_id="proj-1",
                title="Full task",
                description="Detailed description",
                priority="high",
                status="in_progress",
                due_date="2025-12-31T00:00:00",
                assignee_id="user-1",
                parent_task_id="t-parent",
                tags=["bug", "urgent"],
            )

        body = mock_br.call_args.kwargs["json"]
        assert body["description"] == "Detailed description"
        assert body["priority"] == "high"
        assert body["status"] == "in_progress"
        assert body["due_date"] == "2025-12-31T00:00:00"
        assert body["assignee_id"] == "user-1"
        assert body["parent_task_id"] == "t-parent"
        assert body["tags"] == ["bug", "urgent"]

    async def test_scope_check_enforced(self):
        """create_task respects project scopes."""
        with _auth_mock(scopes=["proj-other"]):
            with pytest.raises(McpAuthError):
                await create_task(project_id="proj-1", title="Test")


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------

class TestUpdateTask:

    async def test_valid_update(self):
        """update_task sends PATCH with only non-None fields."""
        task = {"id": "t1", "project_id": "proj-1"}
        updated = {"id": "t1", "title": "Updated", "status": "done"}
        with _auth_mock(), _br_mock(side_effect=[task, updated]) as mock_br:
            result = await update_task(task_id="t1", title="Updated", status="done")

        assert result == updated
        # First call: GET to fetch task for scope check
        assert mock_br.call_args_list[0].args == ("GET", "/tasks/t1")
        # Second call: PATCH with updates
        patch_call = mock_br.call_args_list[1]
        assert patch_call.args == ("PATCH", "/tasks/t1")
        assert patch_call.kwargs["json"] == {"title": "Updated", "status": "done"}

    async def test_invalid_status_raises_tool_error(self):
        """update_task raises ToolError for invalid status values."""
        with _auth_mock():
            with pytest.raises(ToolError, match="Invalid status"):
                await update_task(task_id="t1", status="invalid_status")

    async def test_invalid_priority_raises_tool_error(self):
        """update_task raises ToolError for invalid priority values."""
        with _auth_mock():
            with pytest.raises(ToolError, match="Invalid priority"):
                await update_task(task_id="t1", priority="super_high")

    async def test_scope_check_after_fetch(self):
        """update_task checks scope against the fetched task's project_id."""
        task = {"id": "t1", "project_id": "proj-restricted"}
        with _auth_mock(scopes=["proj-1"]), _br_mock(return_value=task):
            with pytest.raises(McpAuthError, match="No access to project proj-restricted"):
                await update_task(task_id="t1", title="Nope")


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------

class TestDeleteTask:

    async def test_successful_delete(self):
        """delete_task returns success dict after deletion."""
        task = {"id": "t1", "project_id": "proj-1"}
        # backend_request calls: GET (fetch), DELETE (returns None for 204 or similar)
        with _auth_mock(), _br_mock(side_effect=[task, None]) as mock_br:
            result = await delete_task(task_id="t1")

        assert result == {"success": True, "task_id": "t1"}
        assert mock_br.call_args_list[1].args == ("DELETE", "/tasks/t1")

    async def test_scope_check(self):
        """delete_task checks project scope before deleting."""
        task = {"id": "t1", "project_id": "proj-restricted"}
        with _auth_mock(scopes=["proj-1"]), _br_mock(return_value=task):
            with pytest.raises(McpAuthError):
                await delete_task(task_id="t1")


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------

class TestCompleteTask:

    async def test_marks_task_done(self):
        """complete_task sends PATCH with status=done."""
        task = {"id": "t1", "project_id": "proj-1"}
        completed = {"id": "t1", "status": "done"}
        with _auth_mock(), _br_mock(side_effect=[task, completed]) as mock_br:
            result = await complete_task(task_id="t1")

        assert result == completed
        patch_call = mock_br.call_args_list[1]
        assert patch_call.args == ("PATCH", "/tasks/t1")
        assert patch_call.kwargs["json"] == {"status": "done"}

    async def test_scope_check(self):
        """complete_task checks project scope before completing."""
        task = {"id": "t1", "project_id": "proj-99"}
        with _auth_mock(scopes=["proj-1"]), _br_mock(return_value=task):
            with pytest.raises(McpAuthError):
                await complete_task(task_id="t1")


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------

class TestAddComment:

    async def test_adds_comment_with_author(self):
        """add_comment sends POST with content and author_name='Claude'."""
        task = {"id": "t1", "project_id": "proj-1"}
        comment = {"id": "c1", "content": "Done", "author_name": "Claude"}
        with _auth_mock(), _br_mock(side_effect=[task, comment]) as mock_br:
            result = await add_comment(task_id="t1", content="Done")

        assert result == comment
        post_call = mock_br.call_args_list[1]
        assert post_call.args == ("POST", "/tasks/t1/comments")
        body = post_call.kwargs["json"]
        assert body["content"] == "Done"
        assert body["author_name"] == "Claude"

    async def test_scope_check(self):
        """add_comment checks project scope before adding."""
        task = {"id": "t1", "project_id": "proj-restricted"}
        with _auth_mock(scopes=["proj-1"]), _br_mock(return_value=task):
            with pytest.raises(McpAuthError):
                await add_comment(task_id="t1", content="Test")


# ---------------------------------------------------------------------------
# search_tasks
# ---------------------------------------------------------------------------

class TestSearchTasks:

    async def test_search_with_project_id(self):
        """search_tasks delegates to backend /tasks/search with project_ids param."""
        search_result = {"items": [{"id": "t1", "title": "Fix login bug"}], "total": 1, "limit": 50, "skip": 0}
        with _auth_mock(), _br_mock(return_value=search_result) as mock_br:
            result = await search_tasks(query="login", project_id="proj-1")

        assert result == search_result
        mock_br.assert_awaited_once_with(
            "GET", "/tasks/search",
            params={"q": "login", "limit": 50, "skip": 0, "project_ids": "proj-1"},
        )

    async def test_search_without_project_id_no_scopes(self):
        """When no project_id and no scopes, search all projects (no project_ids param)."""
        search_result = {"items": [], "total": 0, "limit": 50, "skip": 0}
        with _auth_mock(scopes=[]), _br_mock(return_value=search_result) as mock_br:
            result = await search_tasks(query="deploy")

        assert result == search_result
        mock_br.assert_awaited_once_with(
            "GET", "/tasks/search",
            params={"q": "deploy", "limit": 50, "skip": 0},
        )

    async def test_search_with_scopes_passes_project_ids(self):
        """When key has scopes but no project_id, search uses scoped project list as project_ids."""
        search_result = {"items": [{"id": "t1", "title": "Match"}], "total": 1, "limit": 50, "skip": 0}
        with _auth_mock(scopes=["proj-1", "proj-2"]), _br_mock(return_value=search_result) as mock_br:
            result = await search_tasks(query="match")

        mock_br.assert_awaited_once_with(
            "GET", "/tasks/search",
            params={"q": "match", "limit": 50, "skip": 0, "project_ids": "proj-1,proj-2"},
        )

    async def test_search_with_status_filter(self):
        """search_tasks forwards status as task_status param."""
        search_result = {"items": [], "total": 0, "limit": 50, "skip": 0}
        with _auth_mock(), _br_mock(return_value=search_result) as mock_br:
            await search_tasks(query="test", status="todo", project_id="proj-1")

        call_params = mock_br.call_args.kwargs["params"]
        assert call_params["task_status"] == "todo"

    async def test_search_with_pagination(self):
        """search_tasks forwards limit and skip."""
        search_result = {"items": [], "total": 100, "limit": 10, "skip": 20}
        with _auth_mock(), _br_mock(return_value=search_result) as mock_br:
            result = await search_tasks(query="test", limit=10, skip=20)

        assert result["total"] == 100
        call_params = mock_br.call_args.kwargs["params"]
        assert call_params["limit"] == 10
        assert call_params["skip"] == 20

    async def test_search_scope_check_with_project_id(self):
        """search_tasks checks scope when project_id is explicitly provided."""
        with _auth_mock(scopes=["proj-other"]):
            with pytest.raises(McpAuthError):
                await search_tasks(query="test", project_id="proj-1")


# ---------------------------------------------------------------------------
# list_overdue_tasks
# ---------------------------------------------------------------------------

class TestListOverdueTasks:

    async def test_returns_overdue_tasks(self):
        """list_overdue_tasks filters tasks past their due_date."""
        paginated = {"items": [
            {"id": "t1", "title": "Overdue", "due_date": "2020-01-01T00:00:00", "status": "todo"},
            {"id": "t2", "title": "Future", "due_date": "2099-12-31T00:00:00", "status": "todo"},
            {"id": "t3", "title": "No due", "due_date": None, "status": "todo"},
        ], "total": 3, "limit": 50, "skip": 0}
        projects = [{"id": "proj-1"}]

        async def side_effect(method, path, **kwargs):
            if path == "/projects":
                return projects
            return paginated

        with _auth_mock(), _br_mock(side_effect=side_effect):
            result = await list_overdue_tasks()

        assert len(result) == 1
        assert result[0]["id"] == "t1"

    async def test_excludes_done_and_cancelled(self):
        """Overdue tasks with status 'done' or 'cancelled' are excluded."""
        paginated = {"items": [
            {"id": "t1", "title": "Done overdue", "due_date": "2020-01-01T00:00:00", "status": "done"},
            {"id": "t2", "title": "Cancelled overdue", "due_date": "2020-01-01T00:00:00", "status": "cancelled"},
            {"id": "t3", "title": "Active overdue", "due_date": "2020-01-01T00:00:00", "status": "in_progress"},
        ], "total": 3, "limit": 50, "skip": 0}
        projects = [{"id": "proj-1"}]

        async def side_effect(method, path, **kwargs):
            if path == "/projects":
                return projects
            return paginated

        with _auth_mock(), _br_mock(side_effect=side_effect):
            result = await list_overdue_tasks()

        assert len(result) == 1
        assert result[0]["id"] == "t3"

    async def test_sorted_by_due_date(self):
        """Results are sorted by due_date ascending."""
        paginated = {"items": [
            {"id": "t2", "title": "Later", "due_date": "2020-06-01T00:00:00", "status": "todo"},
            {"id": "t1", "title": "Earlier", "due_date": "2020-01-01T00:00:00", "status": "todo"},
        ], "total": 2, "limit": 50, "skip": 0}
        projects = [{"id": "proj-1"}]

        async def side_effect(method, path, **kwargs):
            if path == "/projects":
                return projects
            return paginated

        with _auth_mock(), _br_mock(side_effect=side_effect):
            result = await list_overdue_tasks()

        assert result[0]["id"] == "t1"
        assert result[1]["id"] == "t2"

    async def test_with_project_id(self):
        """list_overdue_tasks scoped to a specific project."""
        paginated = {"items": [
            {"id": "t1", "title": "Overdue", "due_date": "2020-01-01T00:00:00", "status": "todo"},
        ], "total": 1, "limit": 50, "skip": 0}
        with _auth_mock(), _br_mock(return_value=paginated):
            result = await list_overdue_tasks(project_id="proj-1")

        assert len(result) == 1

    async def test_with_limit(self):
        """list_overdue_tasks respects the limit param."""
        paginated = {"items": [
            {"id": "t1", "due_date": "2020-01-01T00:00:00", "status": "todo"},
            {"id": "t2", "due_date": "2020-02-01T00:00:00", "status": "todo"},
            {"id": "t3", "due_date": "2020-03-01T00:00:00", "status": "todo"},
        ], "total": 3, "limit": 50, "skip": 0}
        projects = [{"id": "proj-1"}]

        async def side_effect(method, path, **kwargs):
            if path == "/projects":
                return projects
            return paginated

        with _auth_mock(), _br_mock(side_effect=side_effect):
            result = await list_overdue_tasks(limit=2)

        assert len(result) == 2

    async def test_scope_check_with_project_id(self):
        """list_overdue_tasks checks scope when project_id is given."""
        with _auth_mock(scopes=["proj-other"]):
            with pytest.raises(McpAuthError):
                await list_overdue_tasks(project_id="proj-1")


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------

class TestListUsers:

    async def test_returns_user_list(self):
        """list_users returns the user list from backend."""
        users = [{"id": "u1", "name": "Alice"}, {"id": "u2", "name": "Bob"}]
        with _auth_mock(), _br_mock(return_value=users) as mock_br:
            result = await list_users()

        assert result == users
        mock_br.assert_awaited_once_with("GET", "/users")


# ---------------------------------------------------------------------------
# batch_create_tasks
# ---------------------------------------------------------------------------

class TestBatchCreateTasks:

    async def test_batch_create_delegates_to_backend(self):
        """batch_create_tasks sends POST to batch endpoint."""
        batch_result = {"created": [{"id": "t1"}, {"id": "t2"}], "failed": []}
        tasks_input = [
            {"title": "Task 1", "priority": "high"},
            {"title": "Task 2"},
        ]
        with _auth_mock(), _br_mock(return_value=batch_result) as mock_br:
            result = await batch_create_tasks(project_id="proj-1", tasks=tasks_input)

        assert result == batch_result
        mock_br.assert_awaited_once_with("POST", "/projects/proj-1/tasks/batch", json=tasks_input)

    async def test_batch_create_scope_check(self):
        """batch_create_tasks checks project scope."""
        with _auth_mock(scopes=["proj-other"]):
            with pytest.raises(McpAuthError):
                await batch_create_tasks(project_id="proj-1", tasks=[{"title": "Test"}])


# ---------------------------------------------------------------------------
# batch_update_tasks
# ---------------------------------------------------------------------------

class TestBatchUpdateTasks:

    async def test_batch_update_delegates_to_backend(self):
        """batch_update_tasks sends PATCH to batch endpoint (unscoped key)."""
        batch_result = {"updated": [{"id": "t1"}, {"id": "t2"}], "failed": []}
        updates_input = [
            {"task_id": "t1", "status": "done"},
            {"task_id": "t2", "priority": "high"},
        ]
        with _auth_mock(scopes=[]), _br_mock(return_value=batch_result) as mock_br:
            result = await batch_update_tasks(updates=updates_input)

        assert result == batch_result
        mock_br.assert_awaited_once_with("PATCH", "/tasks/batch", json=updates_input)

    async def test_batch_update_with_scopes_checks_access(self):
        """batch_update_tasks fetches each task and checks scope when scoped."""
        task1 = {"id": "t1", "project_id": "proj-1"}
        task2 = {"id": "t2", "project_id": "proj-1"}
        batch_result = {"updated": [{"id": "t1"}, {"id": "t2"}], "failed": []}
        updates_input = [
            {"task_id": "t1", "status": "done"},
            {"task_id": "t2", "priority": "high"},
        ]

        async def side_effect(method, path, **kwargs):
            if method == "GET" and "/tasks/t1" in path:
                return task1
            if method == "GET" and "/tasks/t2" in path:
                return task2
            if method == "PATCH":
                return batch_result
            return None

        with _auth_mock(scopes=["proj-1"]), _br_mock(side_effect=side_effect):
            result = await batch_update_tasks(updates=updates_input)

        assert result == batch_result

    async def test_batch_update_scope_denied(self):
        """batch_update_tasks raises when a task is in a denied project."""
        task1 = {"id": "t1", "project_id": "proj-restricted"}
        updates_input = [{"task_id": "t1", "status": "done"}]

        with _auth_mock(scopes=["proj-1"]), _br_mock(return_value=task1):
            with pytest.raises(McpAuthError, match="No access to project proj-restricted"):
                await batch_update_tasks(updates=updates_input)
