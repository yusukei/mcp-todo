import logging
from datetime import UTC, datetime

from fastmcp.exceptions import ToolError

from ...models import Project, Task, User
from ...models.project import ProjectStatus
from ...models.task import Comment, TaskPriority, TaskStatus
from ...services.events import publish_event
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _resolve_project_id

logger = logging.getLogger(__name__)


def _task_dict(t: Task) -> dict:
    return {
        "id": str(t.id),
        "project_id": t.project_id,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "assignee_id": t.assignee_id,
        "parent_task_id": t.parent_task_id,
        "tags": t.tags,
        "comments": [
            {"id": c.id, "content": c.content, "author_id": c.author_id,
             "author_name": c.author_name, "created_at": c.created_at.isoformat()}
            for c in t.comments
        ],
        "created_by": t.created_by,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "needs_detail": t.needs_detail,
        "approved": t.approved,
        "sort_order": t.sort_order,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


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
        status: Filter: todo / in_progress / in_review / done / cancelled
        priority: Filter: low / medium / high / urgent
        assignee_id: Filter by assignee user ID
        tag: Filter by tag name
        needs_detail: Filter by needs_detail flag (true/false)
        approved: Filter by approved flag (true/false)
        limit: Maximum number of tasks to return (default 50)
        skip: Number of tasks to skip for pagination (default 0)
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    query = Task.find(Task.project_id == project_id, Task.is_deleted == False)  # noqa: E712
    if status:
        query = query.find(Task.status == TaskStatus(status))
    if priority:
        query = query.find(Task.priority == TaskPriority(priority))
    if assignee_id:
        query = query.find(Task.assignee_id == assignee_id)
    if tag:
        query = query.find({"tags": tag})
    if needs_detail is not None:
        query = query.find(Task.needs_detail == needs_detail)
    if approved is not None:
        query = query.find(Task.approved == approved)

    total = await query.count()
    tasks = await query.sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list()
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@mcp.tool()
async def get_task(task_id: str) -> dict:
    """Get detailed information about a task.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, key_info["project_scopes"])
    return _task_dict(task)


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
        status: Initial status (todo / in_progress / in_review / done / cancelled)
        due_date: Due date in ISO 8601 format (e.g. 2025-12-31T00:00:00)
        assignee_id: Assignee user ID
        parent_task_id: Parent task ID (for subtasks)
        tags: List of tag names
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    parsed_due_date = None
    if due_date:
        parsed_due_date = datetime.fromisoformat(due_date)

    task = Task(
        project_id=project_id,
        title=title,
        description=description,
        priority=TaskPriority(priority),
        status=TaskStatus(status),
        due_date=parsed_due_date,
        assignee_id=assignee_id,
        parent_task_id=parent_task_id,
        tags=tags or [],
        created_by="mcp",
    )
    await task.insert()
    await publish_event(project_id, "task.created", _task_dict(task))
    return _task_dict(task)


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
        status: New status (todo / in_progress / in_review / done / cancelled)
        due_date: New due date (ISO 8601 format)
        assignee_id: New assignee user ID
        tags: New tag list
    """
    key_info = await authenticate()

    VALID_STATUSES = {"todo", "in_progress", "in_review", "done", "cancelled"}
    VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    if status is not None and status not in VALID_STATUSES:
        raise ToolError(f"Invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}")
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ToolError(f"Invalid priority '{priority}'. Valid: {', '.join(sorted(VALID_PRIORITIES))}")

    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, key_info["project_scopes"])

    updates: dict = {}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["description"] = description
    if priority is not None:
        updates["priority"] = priority
    if status is not None:
        updates["status"] = status
    if due_date is not None:
        updates["due_date"] = due_date
    if assignee_id is not None:
        updates["assignee_id"] = assignee_id
    if tags is not None:
        updates["tags"] = tags

    for field, value in updates.items():
        if field == "status":
            new_status = TaskStatus(value)
            if new_status == TaskStatus.done and task.status != TaskStatus.done:
                task.completed_at = datetime.now(UTC)
            elif new_status != TaskStatus.done:
                task.completed_at = None
            task.status = new_status
        elif field == "due_date":
            task.due_date = datetime.fromisoformat(value) if value else None
        elif field == "priority":
            task.priority = TaskPriority(value)
        else:
            setattr(task, field, value)

    if updates.get("needs_detail"):
        task.approved = False
    if updates.get("approved"):
        task.needs_detail = False

    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@mcp.tool()
async def delete_task(task_id: str) -> dict:
    """Delete a task (soft delete).

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, key_info["project_scopes"])

    task.is_deleted = True
    await task.save_updated()
    await publish_event(task.project_id, "task.deleted", {"id": task_id})
    return {"success": True, "task_id": task_id}


@mcp.tool()
async def complete_task(task_id: str) -> dict:
    """Mark a task as done.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, key_info["project_scopes"])

    if task.status != TaskStatus.done:
        task.status = TaskStatus.done
        task.completed_at = datetime.now(UTC)
        await task.save_updated()
        await publish_event(task.project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@mcp.tool()
async def reopen_task(task_id: str) -> dict:
    """Reopen a completed or cancelled task (set status back to todo).

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, key_info["project_scopes"])

    task.status = TaskStatus.todo
    task.completed_at = None
    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@mcp.tool()
async def add_comment(task_id: str, content: str) -> dict:
    """Add a comment to a task.

    Args:
        task_id: Task ID
        content: Comment body text
    """
    key_info = await authenticate()
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, key_info["project_scopes"])

    comment = Comment(content=content, author_id="mcp", author_name="Claude")
    task.comments.append(comment)
    await task.save_updated()
    await publish_event(task.project_id, "comment.added", {"task_id": task_id, "comment": {
        "id": comment.id, "content": comment.content,
        "author_id": comment.author_id, "author_name": comment.author_name,
        "created_at": comment.created_at.isoformat(),
    }})
    return _task_dict(task)


@mcp.tool()
async def delete_comment(task_id: str, comment_id: str) -> dict:
    """Delete a comment from a task.

    Args:
        task_id: Task ID
        comment_id: Comment ID to delete
    """
    key_info = await authenticate()
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, key_info["project_scopes"])

    comment = next((c for c in task.comments if c.id == comment_id), None)
    if not comment:
        raise ToolError("Comment not found")

    task.comments = [c for c in task.comments if c.id != comment_id]
    await task.save_updated()
    await publish_event(task.project_id, "comment.deleted", {
        "task_id": task_id, "comment_id": comment_id,
    })
    return _task_dict(task)


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

    filters: dict = {
        "$or": [
            {"title": {"$regex": query, "$options": "i"}},
            {"description": {"$regex": query, "$options": "i"}},
        ],
        "is_deleted": False,
    }

    if project_id:
        project_id = await _resolve_project_id(project_id)
        check_project_access(project_id, scopes)
        filters["project_id"] = project_id
    elif scopes:
        filters["project_id"] = {"$in": scopes}

    if status:
        filters["status"] = TaskStatus(status)
    if needs_detail is not None:
        filters["needs_detail"] = needs_detail
    if approved is not None:
        filters["approved"] = approved

    db_query = Task.find(filters)
    total = await db_query.count()
    tasks = await db_query.skip(skip).limit(limit).to_list()
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


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
    key_info = await authenticate()
    scopes = key_info["project_scopes"]
    now = datetime.now(UTC)

    filters: dict = {
        "is_deleted": False,
        "due_date": {"$lt": now},
        "status": {"$nin": [TaskStatus.done, TaskStatus.cancelled]},
    }

    if project_id:
        project_id = await _resolve_project_id(project_id)
        check_project_access(project_id, scopes)
        filters["project_id"] = project_id
    elif scopes:
        filters["project_id"] = {"$in": scopes}

    tasks = await Task.find(filters).sort(+Task.due_date).limit(limit).to_list()
    return [_task_dict(t) for t in tasks]


@mcp.tool()
async def list_users() -> list[dict]:
    """List all users (for assignee selection)."""
    await authenticate()
    users = await User.find(User.is_active == True).to_list()  # noqa: E712
    return [{"id": str(u.id), "name": u.name, "email": u.email} for u in users]


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
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    created = []
    failed = []
    for item in tasks:
        try:
            parsed_due_date = None
            if item.get("due_date"):
                parsed_due_date = datetime.fromisoformat(item["due_date"])

            task = Task(
                project_id=project_id,
                title=item["title"],
                description=item.get("description", ""),
                priority=TaskPriority(item.get("priority", "medium")),
                status=TaskStatus(item.get("status", "todo")),
                due_date=parsed_due_date,
                assignee_id=item.get("assignee_id"),
                parent_task_id=item.get("parent_task_id"),
                tags=item.get("tags", []),
                created_by=item.get("created_by", "mcp"),
            )
            await task.insert()
            await publish_event(project_id, "task.created", _task_dict(task))
            created.append(_task_dict(task))
        except Exception as e:
            logger.warning("batch_create_tasks: failed to create task '%s': %s", item.get("title"), e)
            failed.append({"title": item.get("title"), "error": str(e)})
    return {"created": created, "failed": failed}


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
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    query = Task.find(Task.project_id == project_id, Task.is_deleted == False)  # noqa: E712
    if flag == "needs_detail":
        query = query.find(Task.needs_detail == True)  # noqa: E712
    elif flag == "approved":
        query = query.find(Task.approved == True)  # noqa: E712
    elif flag == "pending":
        query = query.find(Task.needs_detail == False, Task.approved == False)  # noqa: E712
    elif flag != "all":
        raise ToolError(f"Invalid flag '{flag}'. Valid: needs_detail, approved, pending, all")

    total = await query.count()
    tasks = await query.sort(+Task.sort_order, +Task.created_at).limit(limit).to_list()
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": 0}


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

    updated = []
    failed = []
    for item in updates:
        try:
            task_id = item.get("task_id")
            if not task_id:
                failed.append({"task_id": None, "error": "task_id required"})
                continue

            task = await Task.get(task_id)
            if not task or task.is_deleted:
                failed.append({"task_id": task_id, "error": "Task not found"})
                continue

            check_project_access(task.project_id, scopes)

            fields = {k: v for k, v in item.items() if k != "task_id" and v is not None}
            for field, value in fields.items():
                if field == "status":
                    new_status = TaskStatus(value)
                    if new_status == TaskStatus.done and task.status != TaskStatus.done:
                        task.completed_at = datetime.now(UTC)
                    elif new_status != TaskStatus.done:
                        task.completed_at = None
                    task.status = new_status
                elif field == "due_date":
                    task.due_date = datetime.fromisoformat(value) if value else None
                elif field == "priority":
                    task.priority = TaskPriority(value)
                else:
                    setattr(task, field, value)

            if fields.get("needs_detail"):
                task.approved = False
            if fields.get("approved"):
                task.needs_detail = False

            await task.save_updated()
            await publish_event(task.project_id, "task.updated", _task_dict(task))
            updated.append(_task_dict(task))
        except Exception as e:
            logger.warning("batch_update_tasks: failed to update task '%s': %s", item.get("task_id"), e)
            failed.append({"task_id": item.get("task_id"), "error": str(e)})
    return {"updated": updated, "failed": failed}
