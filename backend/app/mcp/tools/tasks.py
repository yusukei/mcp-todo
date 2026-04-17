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
from ...services.task_approval import cascade_approve_subtasks
from ...services.task_links import (
    CrossProjectError,
    CycleError,
    DuplicateLinkError,
    LinkNotFoundError,
    SelfReferenceError,
    TargetNotFoundError,
    cleanup_dependents,
    link as _link_service,
    list_dependents,
    unlink as _unlink_service,
)
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _check_project_not_locked, _resolve_project_id

logger = logging.getLogger(__name__)


async def _index_task(task: "Task") -> None:
    """タスクを検索インデックスに追加・更新（利用可能な場合のみ）"""
    from ...services.search import index_task
    await index_task(task)


async def _deindex_task(task_id: str) -> None:
    """タスクを検索インデックスから削除（利用可能な場合のみ）"""
    from ...services.search import deindex_task
    await deindex_task(task_id)


async def _get_task_or_raise(task_id: str, key_info: dict) -> Task:
    """Fetch a task by ID, verify it exists and is not deleted, and check project access."""
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise ToolError("Task not found")
    await check_project_access(task.project_id, key_info)
    return task


def _task_summary(task: "Task") -> dict:
    """Lightweight task serialization excluding comments and attachments."""
    d = _task_dict(task)
    d.pop("comments", None)
    d.pop("attachments", None)
    return d


def _task_minimal(task: "Task") -> dict:
    """Minimal hub-API task representation.

    Intended for overview calls like ``get_work_context`` where the LLM
    just needs to see "what's on the list". Heavier fields (description,
    tags, comments, completion_report, activity_log) are only fetched via
    ``get_task`` / ``get_task_context`` when the user drills in.

    Included fields:
    - id, title, status, priority
    - due_date (when set)
    - assignee_id (when set)
    - needs_detail / approved (when truthy)
    """
    d: dict = {
        "id": str(task.id),
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
    }
    if getattr(task, "due_date", None):
        d["due_date"] = task.due_date.isoformat()
    if getattr(task, "assignee_id", None):
        d["assignee_id"] = task.assignee_id
    if getattr(task, "needs_detail", False):
        d["needs_detail"] = True
    if getattr(task, "approved", False):
        d["approved"] = True
    return d


def _task_serializer(detail: str):
    """Resolve a detail level (``"minimal"``/``"summary"``/``"full"``) to the
    matching task serializer."""
    if detail == "minimal":
        return _task_minimal
    if detail == "summary":
        return _task_summary
    if detail == "full":
        return _task_dict
    raise ToolError("detail must be 'minimal', 'summary', or 'full'")


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


def _parse_decision_context(value: dict | None) -> DecisionContext | None:
    """Parse a decision_context dict into a DecisionContext model."""
    if not value or value == {}:
        return None
    return DecisionContext(
        background=value.get("background", ""),
        decision_point=value.get("decision_point", ""),
        options=[
            DecisionOption(label=o.get("label", ""), description=o.get("description", ""))
            for o in value.get("options", [])
        ],
        recommendation=value.get("recommendation"),
    )


UPDATABLE_FIELDS = {
    "title", "description", "status", "priority", "due_date",
    "assignee_id", "tags", "needs_detail", "approved", "sort_order", "archived",
    "completion_report", "task_type", "decision_context", "active_form",
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
    updated_since: str | None = None,
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
        status: Filter: todo / in_progress / on_hold / done / cancelled
        priority: Filter: low / medium / high / urgent
        task_type: Filter by task type: action / decision
        assignee_id: Filter by assignee user ID
        tag: Filter by tag name
        needs_detail: Filter by needs_detail flag (true/false)
        approved: Filter by approved flag (true/false)
        archived: Filter by archived flag (true/false). Default false (hides archived). Set to null/omit to include all.
        due_before: Filter tasks due before this date. Supports ISO 8601, or shorthands: today, tomorrow, yesterday, this_week, next_week, this_month, +7d, -3d
        due_after: Filter tasks due after this date. Supports ISO 8601, or shorthands: today, tomorrow, yesterday, this_week, next_week, this_month, +7d, -3d
        updated_since: Return only tasks whose ``updated_at`` is strictly greater than this timestamp (ISO 8601). Used for SSE reconcile after short disconnects.
        sort_by: Sort field: sort_order (default) / created_at / due_date / priority / updated_at
        order: Sort direction: asc (default) / desc
        limit: Maximum number of tasks to return (default 50)
        skip: Number of tasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    filters: dict = {
        "project_id": project_id,
        "is_deleted": False,
    }
    if status:
        filters["status"] = TaskStatus(status)
    if priority:
        filters["priority"] = TaskPriority(priority)
    if assignee_id:
        filters["assignee_id"] = assignee_id
    if tag:
        filters["tags"] = tag
    if task_type:
        filters["task_type"] = TaskType(task_type)
    if needs_detail is not None:
        filters["needs_detail"] = needs_detail
    if approved is not None:
        filters["approved"] = approved
    if archived is not None:
        filters["archived"] = archived
    if due_before:
        filters.setdefault("due_date", {})["$lte"] = _parse_date_filter(due_before)
    if due_after:
        filters.setdefault("due_date", {})["$gte"] = _parse_date_filter(due_after)
    if updated_since:
        try:
            since = datetime.fromisoformat(updated_since)
        except ValueError:
            raise ToolError(f"Invalid updated_since '{updated_since}': expected ISO 8601")
        filters["updated_at"] = {"$gt": since}

    query = Task.find(filters)

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
    task = await _get_task_or_raise(task_id, key_info)
    return _task_dict(task)


@mcp.tool()
async def get_task_context(task_id: str, activity_limit: int = 20) -> dict:
    """Get full context of a task in a single call: task details, subtasks, and activity log.

    Combines get_task + get_subtasks + get_task_activity into one request
    to reduce MCP round-trips.

    Args:
        task_id: Task ID
        activity_limit: Maximum number of activity log entries (default 20, most recent first)
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info)

    subtasks = await Task.find(
        Task.parent_task_id == task_id,
        Task.is_deleted == False,  # noqa: E712
    ).sort(+Task.sort_order, +Task.created_at).to_list()

    activity_entries = sorted(task.activity_log, key=lambda e: e.changed_at, reverse=True)[:activity_limit]

    parent = None
    if task.parent_task_id:
        parent_task = await Task.get(task.parent_task_id)
        if parent_task and not parent_task.is_deleted:
            parent = {"id": str(parent_task.id), "title": parent_task.title, "status": parent_task.status}

    return {
        "task": _task_dict(task),
        "parent": parent,
        "subtasks": {
            "items": [_task_summary(t) for t in subtasks],
            "total": len(subtasks),
        },
        "activity": {
            "entries": [
                {
                    "field": e.field,
                    "old_value": e.old_value,
                    "new_value": e.new_value,
                    "changed_by": e.changed_by,
                    "changed_at": e.changed_at.isoformat(),
                }
                for e in activity_entries
            ],
            "total": len(task.activity_log),
        },
    }


@mcp.tool()
async def get_work_context(
    project_id: str,
    limit: int = 20,
    skip: int = 0,
    detail: str = "minimal",
) -> dict:
    """Get a comprehensive work context for the current session.

    Returns approved tasks, in-progress tasks, overdue tasks, and tasks needing
    investigation in a single call. Designed to be called at session start.

    Args:
        project_id: Project ID or project name (required — cross-project queries
            are not supported; loop over projects from list_projects if needed)
        limit: Maximum number of tasks per category (default 20)
        skip: Number of tasks to skip per category for pagination (default 0)
        detail: Task serialization level.
            - ``"minimal"`` (default): id, title, status, priority, and
              due_date/assignee/flags when set — ideal for overviews.
            - ``"summary"``: all fields except comments/attachments.
            - ``"full"``: everything including comments and attachments.
    """
    key_info = await authenticate()
    now = datetime.now(UTC)
    serialize = _task_serializer(detail)

    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)
    base_filters: dict = {"is_deleted": False, "project_id": project_id}

    approved_q = Task.find({**base_filters, "approved": True, "status": {"$in": ["todo", "in_progress"]}})
    in_progress_q = Task.find({**base_filters, "status": "in_progress"})
    overdue_q = Task.find({**base_filters, "due_date": {"$ne": None, "$lt": now}, "status": {"$nin": ["on_hold", "done", "cancelled"]}})
    needs_detail_q = Task.find({**base_filters, "needs_detail": True, "status": {"$nin": ["done", "cancelled"]}})

    (
        approved_total, approved_tasks,
        in_progress_total, in_progress_tasks,
        overdue_total, overdue_tasks,
        needs_detail_total, needs_detail_tasks,
    ) = await asyncio.gather(
        approved_q.count(),
        approved_q.sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list(),
        in_progress_q.count(),
        in_progress_q.sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list(),
        overdue_q.count(),
        overdue_q.sort(+Task.due_date).skip(skip).limit(limit).to_list(),
        needs_detail_q.count(),
        needs_detail_q.sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list(),
    )

    return {
        "approved": {"items": [serialize(t) for t in approved_tasks], "total": approved_total, "limit": limit, "skip": skip},
        "in_progress": {"items": [serialize(t) for t in in_progress_tasks], "total": in_progress_total, "limit": limit, "skip": skip},
        "overdue": {"items": [serialize(t) for t in overdue_tasks], "total": overdue_total, "limit": limit, "skip": skip},
        "needs_detail": {"items": [serialize(t) for t in needs_detail_tasks], "total": needs_detail_total, "limit": limit, "skip": skip},
    }


@mcp.tool()
async def get_task_activity(task_id: str, limit: int = 20, skip: int = 0) -> dict:
    """Get the change history (activity log) of a task.

    Returns recent field changes such as status transitions, priority changes,
    assignee changes, and flag updates.

    Args:
        task_id: Task ID
        limit: Maximum number of entries to return (default 20, most recent first)
        skip: Number of entries to skip for pagination (default 0)
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info)

    all_entries = sorted(task.activity_log, key=lambda e: e.changed_at, reverse=True)
    entries = all_entries[skip : skip + limit]
    return {
        "task_id": str(task.id),
        "title": task.title,
        "entries": [
            {
                "field": e.field,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "changed_by": e.changed_by,
                "changed_at": e.changed_at.isoformat(),
            }
            for e in entries
        ],
        "total": len(all_entries),
        "limit": limit,
        "skip": skip,
    }


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
        status: Initial status (todo / in_progress / on_hold / done / cancelled)
        task_type: Task type (action / decision). Use "decision" when the task requires user judgment
        decision_context: REQUIRED when task_type="decision". Dict with keys:
            background (str): Background information about the issue — why this decision is needed,
            decision_point (str): What specifically the user needs to decide,
            options (list[dict]): Available choices, each with "label" and optional "description".
            You MUST provide at least background and decision_point when creating a decision task.
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
    if task_type == "decision" and (not decision_context or not decision_context.get("decision_point")):
        raise ToolError(
            "decision_context with at least 'decision_point' is required when task_type='decision'. "
            "Provide: {background: str, decision_point: str, options: [{label, description}]}"
        )

    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)
    await _check_project_not_locked(project_id)
    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    parsed_due_date = None
    if due_date:
        parsed_due_date = datetime.fromisoformat(due_date)

    parsed_decision_context = _parse_decision_context(decision_context)

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
    await _index_task(task)
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
    active_form: str | None = None,
) -> dict:
    """Update a task. Only provided fields are changed.

    Note: Image attachments can be managed via the REST API
    (POST/DELETE /projects/{project_id}/tasks/{task_id}/attachments).

    Args:
        task_id: Task ID
        title: New title
        description: New description (supports Markdown)
        priority: New priority (low / medium / high / urgent)
        status: New status (todo / in_progress / on_hold / done / cancelled)
        task_type: Task type (action / decision). Use "decision" when the task requires user judgment
        decision_context: Decision context for decision-type tasks. Dict with keys:
            background (str): Background information about the issue,
            decision_point (str): What the user needs to decide,
            options (list[dict]): Available choices, each with "label" and optional "description",
            recommendation (str, optional): AI's recommended option or approach.
            Pass null/empty to clear.
        due_date: New due date (ISO 8601 format)
        assignee_id: New assignee user ID
        tags: New tag list
        completion_report: Completion report text (supports Markdown)
        needs_detail: Flag indicating the user cannot decide whether/how to address this task and needs investigation results first. Setting true automatically clears approved.
        approved: Flag indicating the task is approved for implementation. Setting true automatically clears needs_detail.
        active_form: One-line description of what is being done right now (shown in Live Activity Feed while status=in_progress). Max 500 chars. Pass empty string or null to clear.
    """
    if title is not None and len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if description is not None and len(description) > 10000:
        raise ToolError("Description exceeds maximum length of 10000 characters")
    if completion_report is not None and len(completion_report) > 10000:
        raise ToolError("Completion report exceeds maximum length of 10000 characters")
    if active_form is not None and len(active_form) > 500:
        raise ToolError("active_form exceeds maximum length of 500 characters")

    key_info = await authenticate()

    VALID_STATUSES = {"todo", "in_progress", "on_hold", "done", "cancelled"}
    VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    VALID_TASK_TYPES = {"action", "decision"}
    if status is not None and status not in VALID_STATUSES:
        raise ToolError(f"Invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}")
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ToolError(f"Invalid priority '{priority}'. Valid: {', '.join(sorted(VALID_PRIORITIES))}")
    if task_type is not None and task_type not in VALID_TASK_TYPES:
        raise ToolError(f"Invalid task_type '{task_type}'. Valid: {', '.join(sorted(VALID_TASK_TYPES))}")

    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)

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
    if active_form is not None:
        # Empty string clears the field (stored as None) per the docstring convention.
        updates["active_form"] = active_form if active_form else None

    disallowed = set(updates.keys()) - UPDATABLE_FIELDS
    if disallowed:
        raise ToolError(f"Cannot update field(s): {', '.join(sorted(disallowed))}")

    actor = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"
    TRACKED_FIELDS = {"status", "priority", "assignee_id", "task_type", "needs_detail", "approved"}

    for field, value in updates.items():
        if field in TRACKED_FIELDS:
            old = getattr(task, field, None)
            task.record_change(field, str(old) if old is not None else None, str(value), actor)

        if field == "status":
            task.transition_status(TaskStatus(value))
        elif field == "due_date":
            task.due_date = datetime.fromisoformat(value) if value else None
        elif field == "priority":
            task.priority = TaskPriority(value)
        elif field == "task_type":
            task.task_type = TaskType(value)
        elif field == "decision_context":
            task.decision_context = _parse_decision_context(value)
        else:
            setattr(task, field, value)

    if updates.get("needs_detail"):
        task.approved = False
    if updates.get("approved"):
        task.needs_detail = False

    await task.save_updated()
    if updates.get("approved"):
        await cascade_approve_subtasks(str(task.id), actor)
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@mcp.tool()
async def delete_task(task_id: str, force: bool = False) -> dict:
    """Delete a task (soft delete).

    When other tasks list this task in their ``blocked_by`` list, deletion
    is refused by default to prevent dangling references. Pass ``force=True``
    to purge those references and delete anyway.

    Args:
        task_id: Task ID
        force: If True, also remove ``task_id`` from every dependent's
            ``blocked_by`` before deleting. Default False (refuse with a
            ``blocks_dependents`` error listing the dependent task IDs).

    Returns:
        ``{"success": True, "task_id": ..., "cleaned_dependents": [...]}`` on
        success; raises :class:`ToolError` with ``blocks_dependents`` when
        dependents exist and ``force`` is False.
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)

    dependents = await list_dependents(task.project_id, task_id)
    if dependents and not force:
        raise ToolError(
            "blocks_dependents: task is blocking other tasks "
            f"({', '.join(str(d.id) for d in dependents)}). "
            "Pass force=true to purge references and delete anyway."
        )

    cleaned: list[Task] = []
    if dependents and force:
        actor = f"mcp:{key_info.get('key_id', 'unknown')}" if isinstance(key_info, dict) else "mcp"
        cleaned = await cleanup_dependents(task_id, task.project_id, changed_by=actor)

    task.is_deleted = True
    await task.save_updated()
    await publish_event(task.project_id, "task.deleted", {"id": task_id})
    for dep in cleaned:
        await publish_event(task.project_id, "task.updated", _task_dict(dep))
    await _deindex_task(task_id)
    return {
        "success": True,
        "task_id": task_id,
        "cleaned_dependents": [str(d.id) for d in cleaned],
    }


@mcp.tool()
async def link_tasks(source_id: str, target_id: str, relation: str = "blocks") -> dict:
    """Create a dependency: ``source_id`` will block ``target_id``.

    Both tasks must belong to the same project that the caller has access
    to. Attempts that would create a cycle in the ``blocks`` graph are
    rejected with a ``cycle_detected`` error including the cycle path.

    Args:
        source_id: Task that will gain the outgoing ``blocks`` entry.
        target_id: Task that will gain the incoming ``blocked_by`` entry.
        relation: Reserved for future relation types. Only ``"blocks"`` is
            currently accepted.

    Returns:
        ``{"success": True, "source": {...}, "target": {...}}`` with both
        tasks serialized after the update.
    """
    if relation != "blocks":
        raise ToolError(f"Unsupported relation: {relation!r}")

    key_info = await authenticate()
    # Both ends go through access control — ``_get_task_or_raise`` verifies
    # existence + project membership. A caller that can see source but not
    # target cannot create a link across trust boundaries.
    source = await _get_task_or_raise(source_id, key_info)
    target = await _get_task_or_raise(target_id, key_info)
    await _check_project_not_locked(source.project_id)

    actor = f"mcp:{key_info['key_name']}" if isinstance(key_info, dict) and key_info.get("key_name") else "mcp"
    try:
        updated_source, updated_target = await _link_service(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            changed_by=actor,
        )
    except SelfReferenceError as e:
        raise ToolError(f"self_reference: {e}")
    except TargetNotFoundError as e:
        raise ToolError(f"target_not_found: {e}")
    except CrossProjectError as e:
        raise ToolError(f"cross_project: {e}")
    except DuplicateLinkError as e:
        raise ToolError(f"duplicate_link: {e}")
    except CycleError as e:
        path = " -> ".join(e.details.get("path", []))
        raise ToolError(f"cycle_detected: would form cycle {path}")

    await publish_event(source.project_id, "task.linked", {
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
    })
    await publish_event(source.project_id, "task.updated", _task_dict(updated_source))
    await publish_event(source.project_id, "task.updated", _task_dict(updated_target))
    return {
        "success": True,
        "source": _task_dict(updated_source),
        "target": _task_dict(updated_target),
    }


@mcp.tool()
async def unlink_tasks(source_id: str, target_id: str, relation: str = "blocks") -> dict:
    """Remove the ``source_id -> target_id`` dependency if it exists.

    Args:
        source_id: Task whose ``blocks`` list should lose ``target_id``.
        target_id: Task whose ``blocked_by`` list should lose ``source_id``.
        relation: Only ``"blocks"`` is currently supported.

    Returns:
        ``{"success": True, "source": {...}, "target": {...}}`` on success.
        Raises ``link_not_found`` if no such edge exists.
    """
    if relation != "blocks":
        raise ToolError(f"Unsupported relation: {relation!r}")

    key_info = await authenticate()
    source = await _get_task_or_raise(source_id, key_info)
    await _get_task_or_raise(target_id, key_info)
    await _check_project_not_locked(source.project_id)

    actor = f"mcp:{key_info['key_name']}" if isinstance(key_info, dict) and key_info.get("key_name") else "mcp"
    try:
        updated_source, updated_target = await _unlink_service(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            changed_by=actor,
        )
    except LinkNotFoundError as e:
        raise ToolError(f"link_not_found: {e}")

    await publish_event(source.project_id, "task.unlinked", {
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
    })
    await publish_event(source.project_id, "task.updated", _task_dict(updated_source))
    await publish_event(source.project_id, "task.updated", _task_dict(updated_target))
    return {
        "success": True,
        "source": _task_dict(updated_source),
        "target": _task_dict(updated_target),
    }


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
    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)

    actor = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"
    was_done = task.status == TaskStatus.done
    changed = False
    if task.status != TaskStatus.done:
        task.record_change("status", task.status, TaskStatus.done, actor)
        task.status = TaskStatus.done
        task.completed_at = datetime.now(UTC)
        changed = True
    if completion_report is not None:
        task.completion_report = completion_report
        changed = True
    if changed:
        await task.save_updated()
        await publish_event(task.project_id, "task.updated", _task_dict(task))
        # Fire task.completed only on transition INTO done (S2-1).
        if not was_done:
            await publish_event(task.project_id, "task.completed", _task_dict(task))
        await _index_task(task)
        # Resolve linked error issues
        from ...services.error_tracker.lifecycle import resolve_linked_issues
        await resolve_linked_issues(str(task.id))
    return _task_dict(task)


@mcp.tool()
async def reopen_task(task_id: str) -> dict:
    """Reopen a completed or cancelled task (set status back to todo).

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)
    actor = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    task.record_change("status", task.status, TaskStatus.todo, actor)
    task.status = TaskStatus.todo
    task.completed_at = None
    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@mcp.tool()
async def archive_task(task_id: str) -> dict:
    """Archive a task to hide it from the default task list.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)

    task.archived = True
    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    # Ignore linked error issues
    from ...services.error_tracker.lifecycle import ignore_linked_issues
    await ignore_linked_issues(str(task.id))
    return _task_dict(task)


@mcp.tool()
async def unarchive_task(task_id: str) -> dict:
    """Unarchive a task to show it in the default task list again.

    Args:
        task_id: Task ID
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)

    task.archived = False
    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@mcp.tool()
async def add_comment(task_id: str, content: str) -> dict:
    """Add a comment to a task.

    Use this to record investigation findings for needs_detail tasks,
    or to leave notes and updates. Comments preserve the original description intact.

    Args:
        task_id: Task ID
        content: Comment body text
    """
    key_info = await authenticate()
    if len(content) > 10000:
        raise ToolError("Comment content exceeds maximum length of 10000 characters")
    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)

    author_name = key_info.get("key_name", "Claude")
    comment = Comment(content=content, author_id="mcp", author_name=author_name)
    task.comments.append(comment)
    await task.save_updated()
    await publish_event(task.project_id, "comment.added", {"task_id": task_id, "comment": {
        "id": comment.id, "content": comment.content,
        "author_id": comment.author_id, "author_name": comment.author_name,
        "created_at": comment.created_at.isoformat(),
    }})
    await _index_task(task)
    return _task_dict(task)


@mcp.tool()
async def delete_comment(task_id: str, comment_id: str) -> dict:
    """Delete a comment from a task.

    Args:
        task_id: Task ID
        comment_id: Comment ID to delete
    """
    key_info = await authenticate()
    task = await _get_task_or_raise(task_id, key_info)
    await _check_project_not_locked(task.project_id)

    comment = next((c for c in task.comments if c.id == comment_id), None)
    if not comment:
        raise ToolError("Comment not found")

    task.comments = [c for c in task.comments if c.id != comment_id]
    await task.save_updated()
    await publish_event(task.project_id, "comment.deleted", {
        "task_id": task_id, "comment_id": comment_id,
    })
    await _index_task(task)
    return _task_dict(task)


@mcp.tool()
async def search_tasks(
    query: str,
    project_id: str,
    status: str | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
    limit: int = 50,
    skip: int = 0,
    summary: bool = False,
) -> dict:
    """Search tasks by keyword across title, description, tags, and comments.

    Uses Tantivy full-text search with Japanese morphological analysis (Lindera)
    when available, falling back to MongoDB $regex for substring matching.

    Args:
        query: Search keyword (supports Tantivy query syntax when full-text search is available)
        project_id: Project ID or project name (required — cross-project search
            is not supported; loop over projects from list_projects if needed)
        status: Filter by status
        needs_detail: Filter by needs_detail flag (true/false)
        approved: Filter by approved flag (true/false)
        limit: Maximum number of results (default 50)
        skip: Number of results to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()

    resolved_project_id = await _resolve_project_id(project_id)
    await check_project_access(resolved_project_id, key_info)

    # Try Tantivy full-text search first
    from ...services.search import SearchService
    search_svc = SearchService.get_instance()
    if search_svc is not None:
        try:
            project_ids = [resolved_project_id]
            result = await asyncio.to_thread(
                search_svc.search,
                query,
                project_ids=project_ids,
                status=status,
                limit=limit + skip,  # fetch enough to handle skip
                offset=0,
            )

            # Fetch matched tasks from MongoDB (preserving relevance order)
            task_ids = [r["task_id"] for r in result.results]
            if task_ids:
                tasks_by_id: dict[str, Task] = {}
                for t in await Task.find({"_id": {"$in": [__import__("bson").ObjectId(tid) for tid in task_ids]}, "is_deleted": False}).to_list():
                    tasks_by_id[str(t.id)] = t

                # Apply additional filters not handled by Tantivy
                ordered_tasks: list[Task] = []
                for tid in task_ids:
                    t = tasks_by_id.get(tid)
                    if not t:
                        continue
                    if needs_detail is not None and t.needs_detail != needs_detail:
                        continue
                    if approved is not None and t.approved != approved:
                        continue
                    ordered_tasks.append(t)

                # Apply skip/limit
                paged = ordered_tasks[skip:skip + limit]
                serialize = _task_summary if summary else _task_dict
                return {
                    "items": [serialize(t) for t in paged],
                    "total": len(ordered_tasks),
                    "limit": limit,
                    "skip": skip,
                    "_meta": {"search_engine": "tantivy"},
                }
            else:
                return {"items": [], "total": 0, "limit": limit, "skip": skip, "_meta": {"search_engine": "tantivy"}}
        except Exception as e:
            logger.warning("Tantivy search failed, falling back to $regex: %s", e)

    # Fallback: MongoDB $regex
    filters: dict = {
        "$or": [
            {"title": {"$regex": re.escape(query), "$options": "i"}},
            {"description": {"$regex": re.escape(query), "$options": "i"}},
        ],
        "is_deleted": False,
    }

    filters["project_id"] = resolved_project_id

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
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": skip, "_meta": {"search_engine": "regex"}}


@mcp.tool()
async def list_overdue_tasks(
    project_id: str,
    limit: int = 50,
    skip: int = 0,
    summary: bool = False,
) -> dict:
    """List overdue tasks (past their due date and not completed).

    Args:
        project_id: Project ID or project name (required — cross-project queries
            are not supported; loop over projects from list_projects if needed)
        limit: Maximum number of results (default 50)
        skip: Number of tasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    now = datetime.now(UTC)

    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    filters: dict = {
        "is_deleted": False,
        "project_id": project_id,
        "due_date": {"$ne": None, "$lt": now},
        "status": {"$nin": [TaskStatus.on_hold, TaskStatus.done, TaskStatus.cancelled]},
    }

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
    await check_project_access(project_id, key_info)
    await _check_project_not_locked(project_id)
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
            if item_task_type == TaskType.decision and (
                not item.get("decision_context") or not item.get("decision_context", {}).get("decision_point")
            ):
                failed.append({"title": item_title, "error": "decision_context with decision_point is required for decision tasks"})
                continue
            parsed_dc = _parse_decision_context(item.get("decision_context"))

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
        for t in task_objects:
            if _task_dict(t) in created:
                await _index_task(t)

    return {"created": created, "failed": failed}


@mcp.tool()
async def list_review_tasks(
    project_id: str,
    flag: str = "all",
    limit: int = 50,
    skip: int = 0,
    summary: bool = False,
) -> dict:
    """List tasks filtered by review flag status within a single project.

    Use this to check which tasks need investigation (needs_detail),
    are approved for implementation (approved), or are awaiting review (pending).
    For needs_detail tasks, the expected action is to investigate and add findings
    as comments — not to implement.
    For a cross-project view of all approved tasks ready for implementation,
    use list_approved_tasks instead.

    Args:
        project_id: Project ID or project name
        flag: Review flag filter: needs_detail / approved / pending (neither flag set) / all
        limit: Maximum number of tasks to return (default 50)
        skip: Number of tasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

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
    tasks = await query.sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list()
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@mcp.tool()
async def list_approved_tasks(
    project_id: str,
    status: str | None = None,
    limit: int = 50,
    skip: int = 0,
    summary: bool = False,
) -> dict:
    """List tasks that have been approved for implementation.

    Use this tool to find tasks that have the 'approved' flag set to true,
    indicating they are ready for implementation. Typically used when the user
    asks to "implement approved tasks" or "work on checked tasks".

    Args:
        project_id: Project ID or project name (required — cross-project queries
            are not supported; loop over projects from list_projects if needed)
        status: Filter by status (todo / in_progress / on_hold / done / cancelled)
        limit: Maximum number of results (default 50)
        skip: Number of tasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()

    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    filters: dict = {
        "approved": True,
        "is_deleted": False,
        "project_id": project_id,
    }

    if status:
        filters["status"] = TaskStatus(status)

    db_query = Task.find(filters)
    total = await db_query.count()
    tasks = await db_query.sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list()
    serialize = _task_summary if summary else _task_dict
    return {"items": [serialize(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@mcp.tool()
async def batch_update_tasks(updates: list[dict]) -> dict:
    """Update multiple tasks at once.

    Args:
        updates: List of update dicts, each with keys: task_id (required),
                 and optional: title, description (supports Markdown),
                 priority, status, due_date, assignee_id, tags
    """
    key_info = await authenticate()
    updates_input_for_cascade = list(updates)

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

            await check_project_access(task.project_id, key_info)
            await _check_project_not_locked(task.project_id)

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
                    task.decision_context = _parse_decision_context(value)
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
                await _index_task(task)

    # Phase 3: publish a single batch event per project
    for pid in project_ids:
        pid_count = sum(1 for t in tasks_to_save if t.project_id == pid and _task_dict(t) in updated)
        await publish_event(pid, "tasks.batch_updated", {"count": pid_count})

    # Cascade approve to subtasks for any task that was just approved
    actor_str = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"
    saved_id_set = {u["id"] for u in updated}
    for item in updates_input_for_cascade:
        tid = item.get("task_id")
        if tid and tid in saved_id_set and item.get("approved") is True:
            await cascade_approve_subtasks(tid, actor_str)

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
        status: Filter: todo / in_progress / on_hold / done / cancelled
        limit: Maximum number of subtasks to return (default 50)
        skip: Number of subtasks to skip for pagination (default 0)
        summary: If true, exclude comments and attachments from response for lighter payload (default false)
    """
    key_info = await authenticate()
    parent = await _get_task_or_raise(task_id, key_info)

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
    await check_project_access(project_id, key_info)

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
    source = await _get_task_or_raise(task_id, key_info)

    target_project_id = source.project_id
    if project_id:
        target_project_id = await _resolve_project_id(project_id)
        await check_project_access(target_project_id, key_info)
    await _check_project_not_locked(target_project_id)

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
    await _index_task(new_task)
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
            await check_project_access(task.project_id, key_info)
            await _check_project_not_locked(task.project_id)

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
                await _index_task(task)
                # Resolve linked error issues
                from ...services.error_tracker.lifecycle import resolve_linked_issues
                await resolve_linked_issues(str(task.id))

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
            await check_project_access(task.project_id, key_info)
            await _check_project_not_locked(task.project_id)

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
                # Ignore linked error issues
                from ...services.error_tracker.lifecycle import ignore_linked_issues
                await ignore_linked_issues(str(task.id))

    for pid in project_ids:
        pid_count = sum(1 for t in tasks_to_save if t.project_id == pid and _task_dict(t) in archived)
        await publish_event(pid, "tasks.batch_updated", {"count": pid_count})

    return {"archived": archived, "failed": failed}
