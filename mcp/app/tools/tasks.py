import logging

from fastmcp.exceptions import ToolError

from ..api_client import backend_request, resolve_project_id
from ..auth import authenticate, check_project_access
from ..server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def list_tasks(
    project_id: str,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List tasks in a project with optional filters.

    Args:
        project_id: Project ID or project name
        status: Filter: todo / in_progress / on_hold / done / cancelled
        priority: Filter: low / medium / high / urgent
        assignee_id: Filter by assignee user ID
        tag: Filter by tag name
        needs_detail: Filter by needs_detail flag (true/false)
        approved: Filter by approved flag (true/false)
        limit: Maximum number of tasks to return (default 50)
        skip: Number of tasks to skip for pagination (default 0)
    """
    key_info = await authenticate()
    project_id = await resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])
    params = {k: v for k, v in {"task_status": status, "priority": priority,
                                  "assignee_id": assignee_id, "tag": tag,
                                  "limit": limit, "skip": skip}.items() if v is not None}
    if needs_detail is not None:
        params["needs_detail"] = str(needs_detail).lower()
    if approved is not None:
        params["approved"] = str(approved).lower()
    return await backend_request("GET", f"/projects/{project_id}/tasks", params=params)


@mcp.tool()
async def get_task(task_id: str) -> dict:
    """Get detailed information about a task.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    return task


@mcp.tool()
async def create_task(
    project_id: str,
    title: str,
    description: str = "",
    priority: str = "medium",
    status: str = "todo",
    due_date: str | None = None,
    assignee_id: str | None = None,
    parent_task_id: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Create a new task in a project.

    Args:
        project_id: Project ID or project name
        title: Task title
        description: Detailed task description
        priority: Priority level (low / medium / high / urgent)
        status: Initial status (todo / in_progress / on_hold / done / cancelled)
        due_date: Due date in ISO 8601 format (e.g. 2025-12-31T00:00:00)
        assignee_id: Assignee user ID
        parent_task_id: Parent task ID (for subtasks)
        tags: List of tag names
    """
    key_info = await authenticate()
    project_id = await resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])
    body = {
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
        "created_by": "mcp",
    }
    if due_date:
        body["due_date"] = due_date
    if assignee_id:
        body["assignee_id"] = assignee_id
    if parent_task_id:
        body["parent_task_id"] = parent_task_id
    if tags:
        body["tags"] = tags
    return await backend_request("POST", f"/projects/{project_id}/tasks", json=body)


@mcp.tool()
async def update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    due_date: str | None = None,
    assignee_id: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Update a task. Only provided fields are changed.

    Args:
        task_id: Task ID
        title: New title
        description: New description
        priority: New priority (low / medium / high / urgent)
        status: New status (todo / in_progress / on_hold / done / cancelled)
        due_date: New due date (ISO 8601 format)
        assignee_id: New assignee user ID
        tags: New tag list
    """
    key_info = await authenticate()
    # Validate enums
    VALID_STATUSES = {"todo", "in_progress", "on_hold", "done", "cancelled"}
    VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    if status is not None and status not in VALID_STATUSES:
        raise ToolError(f"Invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}")
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ToolError(f"Invalid priority '{priority}'. Valid: {', '.join(sorted(VALID_PRIORITIES))}")
    # Scope check: fetch task first
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    body = {k: v for k, v in {
        "title": title, "description": description, "priority": priority,
        "status": status, "due_date": due_date, "assignee_id": assignee_id,
        "tags": tags,
    }.items() if v is not None}
    return await backend_request("PATCH", f"/tasks/{task_id}", json=body)


@mcp.tool()
async def delete_task(task_id: str) -> dict:
    """Delete a task (soft delete).

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    await backend_request("DELETE", f"/tasks/{task_id}")
    return {"success": True, "task_id": task_id}


@mcp.tool()
async def complete_task(task_id: str) -> dict:
    """Mark a task as done.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    return await backend_request("PATCH", f"/tasks/{task_id}", json={"status": "done"})


@mcp.tool()
async def add_comment(task_id: str, content: str) -> dict:
    """Add a comment to a task.

    Args:
        task_id: Task ID
        content: Comment body text
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    return await backend_request("POST", f"/tasks/{task_id}/comments",
                                  json={"content": content, "author_name": "Claude"})


@mcp.tool()
async def search_tasks(
    query: str,
    project_id: str | None = None,
    status: str | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """Search tasks by keyword across title and description.

    Args:
        query: Search keyword
        project_id: Limit search to a specific project by ID or name (omit for all projects)
        status: Filter by status
        needs_detail: Filter by needs_detail flag (true/false)
        approved: Filter by approved flag (true/false)
        limit: Maximum number of results (default 50)
        skip: Number of results to skip for pagination (default 0)
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    params: dict = {"q": query, "limit": limit, "skip": skip}
    if status:
        params["task_status"] = status
    if needs_detail is not None:
        params["needs_detail"] = str(needs_detail).lower()
    if approved is not None:
        params["approved"] = str(approved).lower()

    if project_id:
        project_id = await resolve_project_id(project_id)
        check_project_access(project_id, scopes)
        params["project_ids"] = project_id
    elif scopes:
        params["project_ids"] = ",".join(scopes)
    # If no project_id and no scopes, pass no project_ids (search all)

    return await backend_request("GET", "/tasks/search", params=params)


@mcp.tool()
async def list_overdue_tasks(
    project_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List overdue tasks (past their due date and not completed).

    Args:
        project_id: Limit to a specific project by ID or name (omit for all projects)
        limit: Maximum number of results (default 50)
    """
    from datetime import UTC, datetime

    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    if project_id:
        project_id = await resolve_project_id(project_id)
        check_project_access(project_id, scopes)
        project_ids = [project_id]
    else:
        projects = await backend_request("GET", "/projects",
                                          params={"project_scopes": ",".join(scopes)} if scopes else {})
        project_ids = [p["id"] for p in projects]

    now = datetime.now(UTC).isoformat()
    overdue = []
    for pid in project_ids:
        try:
            resp = await backend_request("GET", f"/projects/{pid}/tasks")
            tasks = resp.get("items", []) if isinstance(resp, dict) else resp
        except Exception:
            logger.warning("Failed to fetch tasks for project %s", pid, exc_info=True)
            continue
        for t in tasks:
            if (t.get("due_date") and t["due_date"] < now
                    and t["status"] not in ("done", "cancelled")):
                overdue.append(t)

    overdue.sort(key=lambda t: t["due_date"])
    return overdue[:limit]


@mcp.tool()
async def list_users() -> list[dict]:
    """List all users (for assignee selection)."""
    await authenticate()
    return await backend_request("GET", "/users")


@mcp.tool()
async def batch_create_tasks(project_id: str, tasks: list[dict]) -> dict:
    """Create multiple tasks at once in a project.

    Args:
        project_id: Project ID or project name
        tasks: List of task dicts, each with keys: title (required),
               description, priority, status, due_date, assignee_id,
               parent_task_id, tags
    """
    key_info = await authenticate()
    project_id = await resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])
    return await backend_request("POST", f"/projects/{project_id}/tasks/batch", json=tasks)


@mcp.tool()
async def list_review_tasks(
    project_id: str,
    flag: str = "all",
    limit: int = 50,
) -> dict:
    """List tasks filtered by review flag status.

    Use this to check which tasks need detail reports or are approved for implementation.

    Args:
        project_id: Project ID or project name
        flag: Review flag filter: needs_detail / approved / pending (neither flag set) / all
        limit: Maximum number of tasks to return (default 50)
    """
    key_info = await authenticate()
    project_id = await resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    params: dict = {"limit": limit}
    if flag == "needs_detail":
        params["needs_detail"] = "true"
    elif flag == "approved":
        params["approved"] = "true"
    elif flag == "pending":
        params["needs_detail"] = "false"
        params["approved"] = "false"
    elif flag != "all":
        raise ToolError(f"Invalid flag '{flag}'. Valid: needs_detail, approved, pending, all")

    return await backend_request("GET", f"/projects/{project_id}/tasks", params=params)


@mcp.tool()
async def batch_update_tasks(updates: list[dict]) -> dict:
    """Update multiple tasks at once.

    Args:
        updates: List of update dicts, each with keys: task_id (required),
                 and optional: title, description, priority, status,
                 due_date, assignee_id, tags
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    # Validate that all referenced tasks are accessible
    if scopes:
        for item in updates:
            task_id = item.get("task_id")
            if task_id:
                task = await backend_request("GET", f"/tasks/{task_id}")
                check_project_access(task["project_id"], scopes)

    return await backend_request("PATCH", "/tasks/batch", json=updates)
