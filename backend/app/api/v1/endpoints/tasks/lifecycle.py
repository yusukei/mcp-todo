"""Task lifecycle transitions: complete, reopen, archive, unarchive."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from .....core.deps import get_current_user
from .....core.validators import valid_object_id
from .....models import Task, User
from .....models.task import TaskStatus
from .....services.events import publish_event
from .....services.search import index_task as _index_task
from .....services.serializers import task_to_dict as _task_dict
from ._shared import CompleteTaskRequest, check_not_locked, check_project_access

router = APIRouter()


async def _get_editable_task(project_id: str, task_id: str, user: User) -> tuple:
    """Shared preamble: validate access and fetch the task."""
    valid_object_id(task_id)
    project = await check_project_access(project_id, user)
    check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return project, task


@router.post("/{task_id}/complete")
async def complete_task(
    project_id: str,
    task_id: str,
    body: CompleteTaskRequest | None = None,
    user: User = Depends(get_current_user),
) -> dict:
    _, task = await _get_editable_task(project_id, task_id, user)
    was_done = task.status == TaskStatus.done
    task.record_change("status", task.status, TaskStatus.done, str(user.id))
    task.status = TaskStatus.done
    task.completed_at = datetime.now(UTC)
    if body and body.completion_report is not None:
        task.completion_report = body.completion_report
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    # Fire task.completed only on transition INTO done — this is the hook
    # clients subscribe to for desktop/audio notifications (S2-6).
    if not was_done:
        await publish_event(project_id, "task.completed", _task_dict(task))
    await _index_task(task)
    # Resolve linked error issues
    from .....services.error_tracker.lifecycle import resolve_linked_issues
    await resolve_linked_issues(str(task.id))
    return _task_dict(task)


@router.post("/{task_id}/reopen")
async def reopen_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    _, task = await _get_editable_task(project_id, task_id, user)
    task.record_change("status", task.status, TaskStatus.todo, str(user.id))
    task.status = TaskStatus.todo
    task.completed_at = None
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.post("/{task_id}/archive")
async def archive_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    _, task = await _get_editable_task(project_id, task_id, user)
    task.archived = True
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    # Ignore linked error issues
    from .....services.error_tracker.lifecycle import ignore_linked_issues
    await ignore_linked_issues(str(task.id))
    return _task_dict(task)


@router.post("/{task_id}/unarchive")
async def unarchive_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    _, task = await _get_editable_task(project_id, task_id, user)
    task.archived = False
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)
