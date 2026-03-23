import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

from fastmcp.exceptions import ToolError

from ...models import Project, Task, User
from ...models.project import ProjectStatus
from ...models.task import Comment, DecisionContext, DecisionOption, TaskPriority, TaskStatus, TaskType
from ...services.events import publish_event
from ...services.serializers import task_to_dict as _task_dict
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _resolve_project_id

logger = logging.getLogger(__name__)

async def _get_task_or_raise(task_id: str, scopes: list[str]) -> Task:
    """Fetch a task by ID, verify it exists and is not deleted, and check project access."""
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    check_project_access(task.project_id, scopes)
    return task


def _task_summary(task: "Task") -> dict:
    """Lightweight task serialization excluding comments and attachments."""
    d = _task_dict(task)
    d.pop("comments", None)
    d.pop("attachments", None)
    return d


def _parse_date_filter(value: str) -> datetime:
    """Parse a date filter value. Supports ISO 8601 and shorthands."""
    shorthands = {
        "today": lambda: datetime.now(UTC).replace(hour=23, minute=59, second=59),
        "tomorrow": lambda: (datetime.now(UTC) + timedelta(days=1)).replace(hour=23, minute=59, second=59),
        "yesterday": lambda: (datetime.now(UTC) - timedelta(days=1)).replace(hour=23, minute=59, second=59),
        "this_week": lambda: datetime.now(UTC) + timedelta(days=(6 - datetime.now(UTC).weekday())),
        "next_week": lambda: datetime.now(UTC) + timedelta(days=(13 - datetime.now(UTC).weekday())),
        "this_month": lambda: (datetime.now(UTC).replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(seconds=1),
    }

    if value in shorthands:
        return shorthands[value]()

    # Relative days: +7d, -3d
    rel_match = re.match(r'^([+-]?\d+)d$', value)
    if rel_match:
        days = int(rel_match.group(1))
        return datetime.now(UTC) + timedelta(days=days)

    # ISO 8601 fallback
    return datetime.fromisoformat(value)


UPDATABLE_FIELDS = {
    "title", "description", "status", "priority", "due_date",
    "assignee_id", "tags", "needs_detail", "approved", "sort_order", "archived",
    "completion_report", "task_type", "decision_context",
}


@mcp.tool()
async def list_tasks(
    project_id: str,
    status: str | None = None,
    priority: str | None = None,
    task_type: str | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
    archived: bool | None = False,
    due_before: str | None = None,
    due_after: str | None = None,
    sort_by: str = "sort_order",
    order: str = "asc",
    limit: int = 50,
    skip: int = 0,
    summary: bool = False,
) -> dict:
    """List tasks in a project with optional filters.

    Use approved=true to list only tasks approved for implementation.
    For a convenient cross-project view of approved tasks, use list_approved_tasks instead.

    Args:
        project_id: Project ID or project name
        status: Filter: todo / in_progress / done / cancelled
        priority: Filter: low / medium / high / urgent
        task_type: Filter by task type: action / decision
        assignee_id: Filter by assignee user ID
        tag: Filter by tag name
        needs_detail: Filter by needs_detail flag (true/false)
        approved: Filter by approved flag (true/false)
        archived: Filter by archived flag (true/false). Default false (hides archived). Set to null/omit to include all.
        due_before: Filter tasks due before this date. Supports ISO 8601, or shorthands: today, tomorrow, yesterday, this_week, next_week, this_month, +7d, -3d
        due_after: Filter tasks due after this date. Supports ISO 8601, or shorthands: today, tomorrow, yesterday, this_week, next_week, this_month, +7d, -3d
        sort_by: Sort field: sort_order (default) / created_at / due_date / priority / updated_at
        order: Sort direction: asc (default) / desc
        limit: Maximum number of tasks to return (default 50)
        skip: Number of tasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
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
    if task_type:
        query = query.find(Task.task_type == TaskType(task_type))
    if needs_detail is not None:
        query = query.find(Task.needs_detail == needs_detail)
    if approved is not None:
        query = query.find(Task.approved == approved)
    if archived is not None:
        query = query.find(Task.archived == archived)
    if due_before:
        query = query.find(Task.due_date <= _parse_date_filter(due_before))
    if due_after:
        query = query.find(Task.due_date >= _parse_date_filter(due_after))

    SORT_FIELDS = {
        "sort_order": Task.sort_order,
        "created_at": Task.created_at,
        "due_date": Task.due_date,
        "priority": Task.priority,
        "updated_at": Task.updated_at,
    }
    sort_field = SORT_FIELDS.get(sort_by)
    if sort_field is None:
        raise ToolError(f"Invalid sort_by '{sort_by}'. Valid: {', '.join(sorted(SORT_FIELDS))}")
    if order not in ("asc", "desc"):
        raise ToolError(f"Invalid order '{order}'. Valid: asc, desc")

    sort_expr = +sort_field if order == "asc" else -sort_field
    # Secondary sort for stability
    secondary = +Task.created_at if sort_by != "created_at" else +Task.sort_order

    total, tasks = await asyncio.gather(
        query.count(),
        query.clone().sort(sort_expr, secondary).skip(skip).limit(limit).to_list(),
    )
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@mcp.tool()
async def get_task(task_id: str) -> dict:
    """Get detailed information about a task.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])
    return _task_dict(task)


@mcp.tool()
async def create_task(
    project_id: str,
    title: str,
    description: str = "",
    priority: str = "medium",
    status: str = "todo",
    task_type: str = "action",
    decision_context: dict | None = None,
    due_date: str | None = None,
    assignee_id: str | None = None,
    parent_task_id: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Create a new task in a project.

    Note: Image attachments can be managed via the REST API
    (POST/DELETE /projects/{project_id}/tasks/{task_id}/attachments).

    Args:
        project_id: Project ID or project name
        title: Task title
        description: Detailed task description (supports Markdown)
        priority: Priority level (low / medium / high / urgent)
        status: Initial status (todo / in_progress / done / cancelled)
        task_type: Task type (action / decision). Use "decision" when the task requires user judgment
        decision_context: Decision context for decision-type tasks. Dict with keys:
            background (str): Background information about the issue,
            decision_point (str): What the user needs to decide,
            options (list[dict]): Available choices, each with "label" and optional "description"
        due_date: Due date in ISO 8601 format (e.g. 2025-12-31T00:00:00)
        assignee_id: Assignee user ID
        parent_task_id: Parent task ID (for subtasks)
        tags: List of tag names
    """
    if len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if len(description) > 10000:
        raise ToolError("Description exceeds maximum length of 10000 characters")
    if task_type not in ("action", "decision"):
        raise ToolError(f"Invalid task_type '{task_type}'. Valid: action, decision")

    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])
    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    parsed_due_date = None
    if due_date:
        parsed_due_date = datetime.fromisoformat(due_date)

    parsed_decision_context = None
    if decision_context:
        parsed_decision_context = DecisionContext(
            background=decision_context.get("background", ""),
            decision_point=decision_context.get("decision_point", ""),
            options=[
                DecisionOption(label=o.get("label", ""), description=o.get("description", ""))
                for o in decision_context.get("options", [])
            ],
        )

    task = Task(
        project_id=project_id,
        title=title,
        description=description,
        priority=TaskPriority(priority),
        status=TaskStatus(status),
        task_type=TaskType(task_type),
        decision_context=parsed_decision_context,
        due_date=parsed_due_date,
        assignee_id=assignee_id,
        parent_task_id=parent_task_id,
        tags=tags or [],
        created_by=creator,
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
    task_type: str | None = None,
    decision_context: dict | None = None,
    due_date: str | None = None,
    assignee_id: str | None = None,
    tags: list[str] | None = None,
    completion_report: str | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
) -> dict:
    """Update a task. Only provided fields are changed.

    Note: Image attachments can be managed via the REST API
    (POST/DELETE /projects/{project_id}/tasks/{task_id}/attachments).

    Args:
        task_id: Task ID
        title: New title
        description: New description (supports Markdown)
        priority: New priority (low / medium / high / urgent)
        status: New status (todo / in_progress / done / cancelled)
        task_type: Task type (action / decision). Use "decision" when the task requires user judgment
        decision_context: Decision context for decision-type tasks. Dict with keys:
            background (str): Background information about the issue,
            decision_point (str): What the user needs to decide,
            options (list[dict]): Available choices, each with "label" and optional "description".
            Pass null/empty to clear.
        due_date: New due date (ISO 8601 format)
        assignee_id: New assignee user ID
        tags: New tag list
        completion_report: Completion report text (supports Markdown)
        needs_detail: Flag indicating task needs more detail before implementation
        approved: Flag indicating task is approved for implementation
    """
    if title is not None and len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if description is not None and len(description) > 10000:
        raise ToolError("Description exceeds maximum length of 10000 characters")
    if completion_report is not None and len(completion_report) > 10000:
        raise ToolError("Completion report exceeds maximum length of 10000 characters")

    key_info = await authenticate()

    VALID_STATUSES = {"todo", "in_progress", "done", "cancelled"}
    VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    VALID_TASK_TYPES = {"action", "decision"}
    if status is not None and status not in VALID_STATUSES:
        raise ToolError(f"Invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}")
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ToolError(f"Invalid priority '{priority}'. Valid: {', '.join(sorted(VALID_PRIORITIES))}")
    if task_type is not None and task_type not in VALID_TASK_TYPES:
        raise ToolError(f"Invalid task_type '{task_type}'. Valid: {', '.join(sorted(VALID_TASK_TYPES))}")

    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

    updates: dict = {}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["description"] = description
    if priority is not None:
        updates["priority"] = priority
    if status is not None:
        updates["status"] = status
    if task_type is not None:
        updates["task_type"] = task_type
    if decision_context is not None:
        updates["decision_context"] = decision_context
    if due_date is not None:
        updates["due_date"] = due_date
    if assignee_id is not None:
        updates["assignee_id"] = assignee_id
    if tags is not None:
        updates["tags"] = tags
    if completion_report is not None:
        updates["completion_report"] = completion_report
    if needs_detail is not None:
        updates["needs_detail"] = needs_detail
    if approved is not None:
        updates["approved"] = approved

    disallowed = set(updates.keys()) - UPDATABLE_FIELDS
    if disallowed:
        raise ToolError(f"Cannot update field(s): {', '.join(sorted(disallowed))}")

    for field, value in updates.items():
        if field == "status":
            task.transition_status(TaskStatus(value))
        elif field == "due_date":
            task.due_date = datetime.fromisoformat(value) if value else None
        elif field == "priority":
            task.priority = TaskPriority(value)
        elif field == "task_type":
            task.task_type = TaskType(value)
        elif field == "decision_context":
            if value is None or value == {}:
                task.decision_context = None
            else:
                task.decision_context = DecisionContext(
                    background=value.get("background", ""),
                    decision_point=value.get("decision_point", ""),
                    options=[
                        DecisionOption(label=o.get("label", ""), description=o.get("description", ""))
                        for o in value.get("options", [])
                    ],
                )
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
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

    task.is_deleted = True
    await task.save_updated()
    await publish_event(task.project_id, "task.deleted", {"id": task_id})
    return {"success": True, "task_id": task_id}


@mcp.tool()
async def complete_task(task_id: str, completion_report: str | None = None) -> dict:
    """Mark a task as done.

    Args:
        task_id: Task ID
        completion_report: Optional completion report text (supports Markdown)
    """
    if completion_report is not None and len(completion_report) > 10000:
        raise ToolError("Completion report exceeds maximum length of 10000 characters")

    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

    changed = False
    if task.status != TaskStatus.done:
        task.status = TaskStatus.done
        task.completed_at = datetime.now(UTC)
        changed = True
    if completion_report is not None:
        task.completion_report = completion_report
        changed = True
    if changed:
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
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

    task.status = TaskStatus.todo
    task.completed_at = None
    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@mcp.tool()
async def archive_task(task_id: str) -> dict:
    """Archive a task to hide it from the default task list.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

    task.archived = True
    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@mcp.tool()
async def unarchive_task(task_id: str) -> dict:
    """Unarchive a task to show it in the default task list again.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

    task.archived = False
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
    if len(content) > 10000:
        raise ToolError("Comment content exceeds maximum length of 10000 characters")
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

    author_name = key_info.get("key_name", "Claude")
    comment = Comment(content=content, author_id="mcp", author_name=author_name)
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
    task = await _get_task_or_raise(task_id, key_info["project_scopes"])

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
    summary: bool = False,
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
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    filters: dict = {
        "$or": [
            {"title": {"$regex": re.escape(query), "$options": "i"}},
            {"description": {"$regex": re.escape(query), "$options": "i"}},
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
    total, tasks = await asyncio.gather(
        db_query.count(),
        db_query.clone().skip(skip).limit(limit).to_list(),
    )
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@mcp.tool()
async def list_overdue_tasks(
    project_id: str | None = None,
    limit: int = 50,
    skip: int = 0,
    summary: bool = False,
) -> dict:
    """List overdue tasks (past their due date and not completed).

    Args:
        project_id: Limit to a specific project by ID or name (omit for all projects)
        limit: Maximum number of results (default 50)
        skip: Number of tasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]
    now = datetime.now(UTC)

    filters: dict = {
        "is_deleted": False,
        "due_date": {"$ne": None, "$lt": now},
        "status": {"$nin": [TaskStatus.done, TaskStatus.cancelled]},
    }

    if project_id:
        project_id = await _resolve_project_id(project_id)
        check_project_access(project_id, scopes)
        filters["project_id"] = project_id
    elif scopes:
        filters["project_id"] = {"$in": scopes}

    db_query = Task.find(filters)
    total, tasks = await asyncio.gather(
        db_query.count(),
        db_query.clone().sort(+Task.due_date).skip(skip).limit(limit).to_list(),
    )
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


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
               description (supports Markdown), priority, status, due_date,
               assignee_id, parent_task_id, tags
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])
    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    created = []
    failed = []
    task_objects = []
    for item in tasks:
        try:
            item_title = item.get("title", "")
            item_desc = item.get("description", "")
            if len(item_title) > 255:
                failed.append({"title": item_title[:50], "error": "Title exceeds maximum length of 255 characters"})
                continue
            if len(item_desc) > 10000:
                failed.append({"title": item_title, "error": "Description exceeds maximum length of 10000 characters"})
                continue

            parsed_due_date = None
            if item.get("due_date"):
                parsed_due_date = datetime.fromisoformat(item["due_date"])

            item_task_type = TaskType(item.get("task_type", "action"))
            item_dc = item.get("decision_context")
            parsed_dc = None
            if item_dc:
                parsed_dc = DecisionContext(
                    background=item_dc.get("background", ""),
                    decision_point=item_dc.get("decision_point", ""),
                    options=[
                        DecisionOption(label=o.get("label", ""), description=o.get("description", ""))
                        for o in item_dc.get("options", [])
                    ],
                )

            task = Task(
                project_id=project_id,
                title=item["title"],
                description=item_desc,
                priority=TaskPriority(item.get("priority", "medium")),
                status=TaskStatus(item.get("status", "todo")),
                task_type=item_task_type,
                decision_context=parsed_dc,
                due_date=parsed_due_date,
                assignee_id=item.get("assignee_id"),
                parent_task_id=item.get("parent_task_id"),
                tags=item.get("tags", []),
                created_by=creator,
            )
            task_objects.append(task)
        except Exception as e:
            logger.warning("batch_create_tasks: failed to create task '%s': %s", item.get("title"), e)
            failed.append({"title": item.get("title"), "error": str(e)})

    if task_objects:
        try:
            await Task.insert_many(task_objects)
            created = [_task_dict(t) for t in task_objects]
        except Exception:
            logger.warning("batch_create_tasks: insert_many failed, falling back to one-by-one")
            for task in task_objects:
                try:
                    await task.insert()
                    created.append(_task_dict(task))
                except Exception as e:
                    logger.warning("batch_create_tasks: failed to insert task '%s': %s", task.title, e)
                    failed.append({"title": task.title, "error": str(e)})

    if created:
        await publish_event(project_id, "tasks.batch_created", {"count": len(created)})

    return {"created": created, "failed": failed}


@mcp.tool()
async def list_review_tasks(
    project_id: str,
    flag: str = "all",
    limit: int = 50,
    summary: bool = False,
) -> dict:
    """List tasks filtered by review flag status within a single project.

    Use this to check which tasks need detail reports or are approved for implementation.
    For a cross-project view of all approved tasks ready for implementation,
    use list_approved_tasks instead.

    Args:
        project_id: Project ID or project name
        flag: Review flag filter: needs_detail / approved / pending (neither flag set) / all
        limit: Maximum number of tasks to return (default 50)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
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
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": 0}


@mcp.tool()
async def list_approved_tasks(
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    summary: bool = False,
) -> dict:
    """List tasks that have been approved for implementation.

    Use this tool to find tasks that have the 'approved' flag set to true,
    indicating they are ready for implementation. Typically used when the user
    asks to "implement approved tasks" or "work on checked tasks".

    Args:
        project_id: Project ID or project name (omit for all projects)
        status: Filter by status (todo / in_progress / done / cancelled)
        limit: Maximum number of results (default 50)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    filters: dict = {
        "approved": True,
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

    db_query = Task.find(filters)
    total = await db_query.count()
    tasks = await db_query.sort(+Task.sort_order, +Task.created_at).limit(limit).to_list()
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": 0}


@mcp.tool()
async def batch_update_tasks(updates: list[dict]) -> dict:
    """Update multiple tasks at once.

    Args:
        updates: List of update dicts, each with keys: task_id (required),
                 and optional: title, description (supports Markdown),
                 priority, status, due_date, assignee_id, tags
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    updated = []
    failed = []
    tasks_to_save = []
    project_ids: set[str] = set()

    # Phase 1: validate and apply field changes sequentially
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
            disallowed = set(fields.keys()) - UPDATABLE_FIELDS
            if disallowed:
                failed.append({"task_id": task_id, "error": f"Cannot update field(s): {', '.join(sorted(disallowed))}"})
                continue

            for field, value in fields.items():
                if field == "status":
                    task.transition_status(TaskStatus(value))
                elif field == "due_date":
                    task.due_date = datetime.fromisoformat(value) if value else None
                elif field == "priority":
                    task.priority = TaskPriority(value)
                elif field == "task_type":
                    task.task_type = TaskType(value)
                elif field == "decision_context":
                    if value is None or value == {}:
                        task.decision_context = None
                    else:
                        task.decision_context = DecisionContext(
                            background=value.get("background", ""),
                            decision_point=value.get("decision_point", ""),
                            options=[
                                DecisionOption(label=o.get("label", ""), description=o.get("description", ""))
                                for o in value.get("options", [])
                            ],
                        )
                else:
                    setattr(task, field, value)

            if fields.get("needs_detail"):
                task.approved = False
            if fields.get("approved"):
                task.needs_detail = False

            tasks_to_save.append(task)
        except Exception as e:
            logger.warning("batch_update_tasks: failed to update task '%s': %s", item.get("task_id"), e)
            failed.append({"task_id": item.get("task_id"), "error": str(e)})

    # Phase 2: save all validated tasks in parallel
    if tasks_to_save:
        results = await asyncio.gather(
            *[t.save_updated() for t in tasks_to_save],
            return_exceptions=True,
        )
        for task, result in zip(tasks_to_save, results):
            if isinstance(result, Exception):
                logger.warning("batch_update_tasks: failed to save task '%s': %s", str(task.id), result)
                failed.append({"task_id": str(task.id), "error": str(result)})
            else:
                updated.append(_task_dict(task))
                project_ids.add(task.project_id)

    # Phase 3: publish a single batch event per project
    for pid in project_ids:
        pid_count = sum(1 for t in tasks_to_save if t.project_id == pid and _task_dict(t) in updated)
        await publish_event(pid, "tasks.batch_updated", {"count": pid_count})

    return {"updated": updated, "failed": failed}


@mcp.tool()
async def get_subtasks(
    task_id: str,
    status: str | None = None,
    limit: int = 50,
    skip: int = 0,
    summary: bool = False,
) -> dict:
    """Get all subtasks of a given parent task.

    Args:
        task_id: Parent task ID
        status: Filter: todo / in_progress / done / cancelled
        limit: Maximum number of subtasks to return (default 50)
        skip: Number of subtasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    parent = await _get_task_or_raise(task_id, key_info["project_scopes"])

    query = Task.find(
        Task.parent_task_id == task_id,
        Task.is_deleted == False,  # noqa: E712
    )
    if status:
        query = query.find(Task.status == TaskStatus(status))

    total, tasks = await asyncio.gather(
        query.count(),
        query.clone().sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list(),
    )
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@mcp.tool()
async def list_tags(project_id: str) -> list[str]:
    """List all unique tags used in a project.

    Args:
        project_id: Project ID or project name
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    pipeline = [
        {"$match": {"project_id": project_id, "is_deleted": False}},
        {"$unwind": "$tags"},
        {"$group": {"_id": "$tags"}},
        {"$sort": {"_id": 1}},
    ]
    results = await Task.get_motor_collection().aggregate(pipeline).to_list(length=None)
    return [doc["_id"] for doc in results]


@mcp.tool()
async def duplicate_task(
    task_id: str,
    project_id: str | None = None,
    title: str | None = None,
) -> dict:
    """Duplicate a task, copying its title, description, priority, tags, and due_date.
    Comments and attachments are not copied.

    Args:
        task_id: Source task ID to duplicate
        project_id: Target project ID or name (defaults to same project)
        title: Override title (defaults to original title with "（コピー）" suffix)
    """
    key_info = await authenticate()
    source = await _get_task_or_raise(task_id, key_info["project_scopes"])

    target_project_id = source.project_id
    if project_id:
        target_project_id = await _resolve_project_id(project_id)
        check_project_access(target_project_id, key_info["project_scopes"])

    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    new_task = Task(
        project_id=target_project_id,
        title=title if title else f"{source.title}（コピー）",
        description=source.description,
        priority=source.priority,
        task_type=source.task_type,
        decision_context=source.decision_context.model_copy() if source.decision_context else None,
        tags=list(source.tags),
        due_date=source.due_date,
        status=TaskStatus.todo,
        created_by=creator,
    )
    await new_task.insert()
    await publish_event(target_project_id, "task.created", _task_dict(new_task))
    return _task_dict(new_task)


@mcp.tool()
async def bulk_complete_tasks(
    task_ids: list[str],
    completion_report: str | None = None,
) -> dict:
    """Mark multiple tasks as done at once.

    Args:
        task_ids: List of task IDs to complete
        completion_report: Optional completion report applied to all tasks
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    completed = []
    failed = []
    tasks_to_save = []
    project_ids: set[str] = set()

    for tid in task_ids:
        try:
            task = await Task.get(tid)
            if not task or task.is_deleted:
                failed.append({"task_id": tid, "error": "Task not found"})
                continue
            check_project_access(task.project_id, scopes)

            if task.status != TaskStatus.done:
                task.status = TaskStatus.done
                task.completed_at = datetime.now(UTC)
            if completion_report is not None:
                task.completion_report = completion_report
            tasks_to_save.append(task)
        except Exception as e:
            logger.warning("bulk_complete_tasks: failed for task '%s': %s", tid, e)
            failed.append({"task_id": tid, "error": str(e)})

    if tasks_to_save:
        results = await asyncio.gather(
            *[t.save_updated() for t in tasks_to_save],
            return_exceptions=True,
        )
        for task, result in zip(tasks_to_save, results):
            if isinstance(result, Exception):
                logger.warning("bulk_complete_tasks: failed to save task '%s': %s", str(task.id), result)
                failed.append({"task_id": str(task.id), "error": str(result)})
            else:
                completed.append(_task_dict(task))
                project_ids.add(task.project_id)

    for pid in project_ids:
        pid_count = sum(1 for t in tasks_to_save if t.project_id == pid and _task_dict(t) in completed)
        await publish_event(pid, "tasks.batch_updated", {"count": pid_count})

    return {"completed": completed, "failed": failed}


@mcp.tool()
async def bulk_archive_tasks(
    task_ids: list[str],
) -> dict:
    """Archive multiple tasks at once.

    Args:
        task_ids: List of task IDs to archive
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    archived = []
    failed = []
    tasks_to_save = []
    project_ids: set[str] = set()

    for tid in task_ids:
        try:
            task = await Task.get(tid)
            if not task or task.is_deleted:
                failed.append({"task_id": tid, "error": "Task not found"})
                continue
            check_project_access(task.project_id, scopes)

            task.archived = True
            tasks_to_save.append(task)
        except Exception as e:
            logger.warning("bulk_archive_tasks: failed for task '%s': %s", tid, e)
            failed.append({"task_id": tid, "error": str(e)})

    if tasks_to_save:
        results = await asyncio.gather(
            *[t.save_updated() for t in tasks_to_save],
            return_exceptions=True,
        )
        for task, result in zip(tasks_to_save, results):
            if isinstance(result, Exception):
                logger.warning("bulk_archive_tasks: failed to save task '%s': %s", str(task.id), result)
                failed.append({"task_id": str(task.id), "error": str(result)})
            else:
                archived.append(_task_dict(task))
                project_ids.add(task.project_id)

    for pid in project_ids:
        pid_count = sum(1 for t in tasks_to_save if t.project_id == pid and _task_dict(t) in archived)
        await publish_event(pid, "tasks.batch_updated", {"count": pid_count})

    return {"archived": archived, "failed": failed}
