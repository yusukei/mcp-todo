"""Task-link endpoints: create/remove ``blocks``/``blocked_by`` dependencies.

Endpoints are mounted by ``tasks/__init__.py`` under
``/projects/{project_id}/tasks``. Only the ``blocks`` relation is accepted;
the reverse side (``blocked_by``) is maintained automatically by the server.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .....core.deps import get_current_user
from .....core.validators import valid_object_id
from .....models import Task, User
from .....services.events import publish_event
from .....services.serializers import task_to_dict as _task_dict
from .....services.task_links import (
    CrossProjectError,
    CycleError,
    DuplicateLinkError,
    LinkNotFoundError,
    SelfReferenceError,
    TargetNotFoundError,
    link as _link,
    unlink as _unlink,
)
from ._shared import check_not_locked, check_project_access

router = APIRouter()


class CreateLinkRequest(BaseModel):
    target_id: str = Field(..., description="Task that will be blocked by the source")
    relation: str = Field("blocks", pattern=r"^blocks$")


async def _load_task(project_id: str, task_id: str) -> Task:
    valid_object_id(task_id)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.post("/{task_id}/links", status_code=status.HTTP_201_CREATED)
async def create_link(
    project_id: str,
    task_id: str,
    body: CreateLinkRequest,
    user: User = Depends(get_current_user),
) -> dict:
    project = await check_project_access(project_id, user)
    check_not_locked(project)
    # Existence/ownership of ``task_id`` is checked here; ``target_id`` is
    # validated inside the service layer so error codes match the API shape.
    await _load_task(project_id, task_id)
    valid_object_id(body.target_id)

    try:
        source, target = await _link(
            source_id=task_id,
            target_id=body.target_id,
            relation=body.relation,
            changed_by=str(user.id),
        )
    except SelfReferenceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.code, "message": str(e), **e.details},
        )
    except TargetNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": e.code, "message": str(e), **e.details},
        )
    except CrossProjectError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.code, "message": str(e), **e.details},
        )
    except DuplicateLinkError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.code, "message": str(e), **e.details},
        )
    except CycleError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": e.code, "message": str(e), **e.details},
        )

    # Publish both the specialized link event and a pair of task.updated
    # events so clients that only observe task.updated still see the new
    # blocks/blocked_by fields without an extra refetch.
    await publish_event(project_id, "task.linked", {
        "source_id": str(source.id),
        "target_id": str(target.id),
        "relation": body.relation,
    })
    await publish_event(project_id, "task.updated", _task_dict(source))
    await publish_event(project_id, "task.updated", _task_dict(target))

    return {"source": _task_dict(source), "target": _task_dict(target)}


@router.delete("/{task_id}/links/{target_id}")
async def delete_link(
    project_id: str,
    task_id: str,
    target_id: str,
    relation: str = Query("blocks", pattern=r"^blocks$"),
    user: User = Depends(get_current_user),
) -> dict:
    project = await check_project_access(project_id, user)
    check_not_locked(project)
    await _load_task(project_id, task_id)
    valid_object_id(target_id)

    try:
        source, target = await _unlink(
            source_id=task_id,
            target_id=target_id,
            relation=relation,
            changed_by=str(user.id),
        )
    except LinkNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": e.code, "message": str(e), **e.details},
        )

    await publish_event(project_id, "task.unlinked", {
        "source_id": str(source.id),
        "target_id": str(target.id),
        "relation": relation,
    })
    await publish_event(project_id, "task.updated", _task_dict(source))
    await publish_event(project_id, "task.updated", _task_dict(target))

    return {"source": _task_dict(source), "target": _task_dict(target)}
