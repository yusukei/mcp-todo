"""Core task CRUD: list, create, read, update, delete."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .....core.deps import get_current_user
from .....core.validators import valid_object_id
from .....models import Task, User
from .....models.task import DecisionContext, DecisionOption, TaskPriority, TaskStatus, TaskType
from .....services.events import publish_event
from .....services.search import deindex_task as _deindex_task, index_task as _index_task
from .....services.serializers import task_to_dict as _task_dict
from .....services.task_approval import cascade_approve_subtasks
from . import _shared
from ._shared import (
    CreateTaskRequest,
    UpdateTaskRequest,
    check_not_locked,
    check_project_access,
)

import asyncio

router = APIRouter()


# ``list_tasks`` and ``create_task`` are registered directly on the parent
# router in ``__init__.py`` — FastAPI refuses include_router() with both the
# sub-router prefix and the route path empty, so root-path routes live on
# the aggregating router instead.


async def list_tasks(
    project_id: str,
    task_status: str | None = Query(None, alias="status"),
    priority: TaskPriority | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
    task_type: TaskType | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
    archived: bool | None = None,
    parent_task_id: str | None = None,
    limit: int | None = Query(None, ge=1),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
) -> dict:
    await check_project_access(project_id, user)

    query = Task.find(Task.project_id == project_id, Task.is_deleted == False)
    if task_status:
        _statuses = [s.strip() for s in task_status.split(",")]
        if len(_statuses) == 1:
            query = query.find(Task.status == _statuses[0])
        else:
            query = query.find({"status": {"$in": _statuses}})
    if priority:
        query = query.find(Task.priority == priority)
    if assignee_id:
        query = query.find(Task.assignee_id == assignee_id)
    if tag:
        query = query.find({"tags": tag})
    if task_type:
        query = query.find(Task.task_type == task_type)
    if needs_detail is not None:
        query = query.find(Task.needs_detail == needs_detail)
    if approved is not None:
        query = query.find(Task.approved == approved)
    if archived is not None:
        query = query.find(Task.archived == archived)
    if parent_task_id is not None:
        query = query.find(Task.parent_task_id == parent_task_id)

    sorted_query = query.clone().sort(+Task.sort_order, +Task.created_at).skip(skip)
    if limit is not None:
        sorted_query = sorted_query.limit(limit)
    total, tasks = await asyncio.gather(query.count(), sorted_query.to_list())
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


async def create_task(
    project_id: str, body: CreateTaskRequest, user: User = Depends(get_current_user)
) -> dict:
    project = await check_project_access(project_id, user)
    check_not_locked(project)

    decision_ctx = None
    if body.decision_context:
        decision_ctx = DecisionContext(
            background=body.decision_context.background,
            decision_point=body.decision_context.decision_point,
            options=[DecisionOption(label=o.label, description=o.description) for o in body.decision_context.options],
        )

    task = Task(
        project_id=project_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        status=body.status,
        task_type=body.task_type,
        decision_context=decision_ctx,
        due_date=body.due_date,
        assignee_id=body.assignee_id,
        parent_task_id=body.parent_task_id,
        tags=body.tags,
        created_by=str(user.id),
    )
    await task.insert()
    await publish_event(project_id, "task.created", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.get("/{task_id}")
async def get_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(task_id)
    await check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_dict(task)


@router.patch("/{task_id}")
async def update_task(
    project_id: str, task_id: str, body: UpdateTaskRequest, user: User = Depends(get_current_user)
) -> dict:
    valid_object_id(task_id)
    project = await check_project_access(project_id, user)
    check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    # Use exclude_unset to distinguish "not sent" from "sent as null".
    # This allows clients to clear fields like due_date and assignee_id by
    # sending null explicitly.
    updates = body.model_dump(exclude_unset=True)
    actor = str(user.id)

    if "title" in updates:
        task.title = updates["title"]
    if "description" in updates:
        task.description = updates["description"]
    if "priority" in updates:
        task.record_change("priority", task.priority, updates["priority"], actor)
        task.priority = updates["priority"]
    if "status" in updates:
        task.record_change("status", task.status, updates["status"], actor)
        task.transition_status(updates["status"])
    if "due_date" in updates:
        task.due_date = updates["due_date"]
    if "assignee_id" in updates:
        task.record_change("assignee_id", task.assignee_id, updates["assignee_id"], actor)
        task.assignee_id = updates["assignee_id"]
    if "tags" in updates:
        task.tags = updates["tags"]
    if "task_type" in updates:
        task.record_change("task_type", task.task_type, updates["task_type"], actor)
        task.task_type = updates["task_type"]
    if "decision_context" in updates:
        dc = updates["decision_context"]
        if dc is None:
            task.decision_context = None
        else:
            task.decision_context = DecisionContext(
                background=dc["background"],
                decision_point=dc["decision_point"],
                options=[DecisionOption(label=o["label"], description=o.get("description", "")) for o in dc.get("options", [])],
            )
    if "needs_detail" in updates:
        task.record_change("needs_detail", str(task.needs_detail), str(updates["needs_detail"]), actor)
        task.needs_detail = updates["needs_detail"]
        if updates["needs_detail"]:
            task.approved = False
    cascade_approved = False
    if "approved" in updates:
        task.record_change("approved", str(task.approved), str(updates["approved"]), actor)
        task.approved = updates["approved"]
        if updates["approved"]:
            task.needs_detail = False
            cascade_approved = True
    if "completion_report" in updates:
        task.completion_report = updates["completion_report"]

    await task.save_updated()
    if cascade_approved:
        await cascade_approve_subtasks(str(task.id), actor)
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> None:
    valid_object_id(task_id)
    project = await check_project_access(project_id, user)
    check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.is_deleted = True
    await task.save_updated()

    # Clean up attachment files from disk
    task_upload_dir = _shared.UPLOADS_DIR / task_id
    if task_upload_dir.exists():
        shutil.rmtree(task_upload_dir, ignore_errors=True)

    await publish_event(project_id, "task.deleted", {"id": task_id})
    await _deindex_task(task_id)
