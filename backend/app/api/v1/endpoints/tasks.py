import asyncio
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ....core.config import settings
from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Project, Task, User
from ....models.task import Attachment, Comment, DecisionContext, DecisionOption, TaskPriority, TaskStatus, TaskType
from ....services.events import publish_event
from ....services.search import deindex_task as _deindex_task, index_task as _index_task
from ....services.serializers import task_to_dict as _task_dict
from ....services.task_export import export_tasks_markdown, export_tasks_pdf

UPLOADS_DIR = Path(settings.UPLOADS_DIR)
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])

MAX_BATCH_SIZE = 100


class DecisionOptionRequest(BaseModel):
    label: str = Field(..., max_length=255)
    description: str = Field("", max_length=2000)


class DecisionContextRequest(BaseModel):
    background: str = Field("", max_length=5000)
    decision_point: str = Field("", max_length=2000)
    options: list[DecisionOptionRequest] = []


class CreateTaskRequest(BaseModel):
    title: str = Field(..., max_length=255)
    description: str = Field("", max_length=10000)
    priority: TaskPriority = TaskPriority.medium
    status: TaskStatus = TaskStatus.todo
    task_type: TaskType = TaskType.action
    decision_context: DecisionContextRequest | None = None
    due_date: datetime | None = None
    assignee_id: str | None = None
    parent_task_id: str | None = None
    tags: list[str] = []


class UpdateTaskRequest(BaseModel):
    title: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=10000)
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    task_type: TaskType | None = None
    decision_context: DecisionContextRequest | None = None
    due_date: datetime | None = None
    assignee_id: str | None = None
    tags: list[str] | None = None
    needs_detail: bool | None = None
    approved: bool | None = None
    completion_report: str | None = Field(None, max_length=10000)


class CompleteTaskRequest(BaseModel):
    completion_report: str | None = Field(None, max_length=10000)


class ExportTasksRequest(BaseModel):
    task_ids: list[str] = Field(..., min_length=1, max_length=50)
    format: str = Field("markdown", pattern=r"^(markdown|pdf)$")


class AddCommentRequest(BaseModel):
    content: str = Field(..., max_length=10000)


async def _check_project_access(project_id: str, user: User) -> Project:
    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return project


def _check_not_locked(project: Project) -> None:
    if project.is_locked:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Project is locked",
        )


@router.post("/export")
async def export_tasks(
    project_id: str,
    body: ExportTasksRequest,
    user: User = Depends(get_current_user),
):
    """Export selected tasks as Markdown or PDF."""
    await _check_project_access(project_id, user)

    try:
        oids = [ObjectId(tid) for tid in body.task_ids]
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid task ID")

    tasks = await Task.find(
        {"_id": {"$in": oids}, "project_id": project_id, "is_deleted": False},
    ).sort("updated_at").to_list()

    if not tasks:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No tasks found")

    if body.format == "markdown":
        md_text = export_tasks_markdown(tasks)
        filename = f"tasks_{project_id[:8]}.md"
        return Response(
            content=md_text.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    pdf_bytes = await export_tasks_pdf(tasks)
    filename = f"tasks_{project_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("")
async def list_tasks(
    project_id: str,
    task_status: TaskStatus | None = Query(None, alias="status"),
    priority: TaskPriority | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
    task_type: TaskType | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
    archived: bool | None = None,
    parent_task_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
) -> dict:
    await _check_project_access(project_id, user)

    query = Task.find(Task.project_id == project_id, Task.is_deleted == False)
    if task_status:
        query = query.find(Task.status == task_status)
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

    total, tasks = await asyncio.gather(
        query.count(),
        query.clone().sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list(),
    )
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


class BatchUpdateItem(BaseModel):
    task_id: str
    needs_detail: bool | None = None
    approved: bool | None = None
    archived: bool | None = None


class BatchUpdateRequest(BaseModel):
    updates: list[BatchUpdateItem] = Field(..., max_length=MAX_BATCH_SIZE)


@router.patch("/batch")
async def batch_update_tasks(
    project_id: str, body: BatchUpdateRequest, user: User = Depends(get_current_user)
) -> dict:
    """Update flags (needs_detail, approved, archived) for multiple tasks in one request."""
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    actor = str(user.id)

    task_ids = [u.task_id for u in body.updates]
    for tid in task_ids:
        valid_object_id(tid)

    tasks = await Task.find(
        {"_id": {"$in": [ObjectId(tid) for tid in task_ids]}},
        Task.project_id == project_id,
        Task.is_deleted == False,
    ).to_list()
    task_map = {str(t.id): t for t in tasks}

    updated = []
    failed = []

    for item in body.updates:
        task = task_map.get(item.task_id)
        if not task:
            failed.append({"task_id": item.task_id, "error": "Task not found"})
            continue

        changes = item.model_dump(exclude_unset=True, exclude={"task_id"})
        if "needs_detail" in changes:
            task.record_change("needs_detail", str(task.needs_detail), str(changes["needs_detail"]), actor)
            task.needs_detail = changes["needs_detail"]
            if changes["needs_detail"]:
                task.approved = False
        if "approved" in changes:
            task.record_change("approved", str(task.approved), str(changes["approved"]), actor)
            task.approved = changes["approved"]
            if changes["approved"]:
                task.needs_detail = False
        if "archived" in changes:
            task.archived = changes["archived"]

        updated.append(task)

    results = await asyncio.gather(
        *[t.save_updated() for t in updated], return_exceptions=True
    )

    saved = []
    for task, result in zip(updated, results):
        if isinstance(result, Exception):
            failed.append({"task_id": str(task.id), "error": str(result)})
        else:
            saved.append(_task_dict(task))
            await _index_task(task)

    if saved:
        await publish_event(project_id, "tasks.batch_updated", {
            "count": len(saved), "task_ids": [t["id"] for t in saved]
        })

    return {"updated": saved, "failed": failed}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    project_id: str, body: CreateTaskRequest, user: User = Depends(get_current_user)
) -> dict:
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

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
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_dict(task)


@router.patch("/{task_id}")
async def update_task(
    project_id: str, task_id: str, body: UpdateTaskRequest, user: User = Depends(get_current_user)
) -> dict:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
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
    if "approved" in updates:
        task.record_change("approved", str(task.approved), str(updates["approved"]), actor)
        task.approved = updates["approved"]
        if updates["approved"]:
            task.needs_detail = False
    if "completion_report" in updates:
        task.completion_report = updates["completion_report"]

    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> None:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.is_deleted = True
    await task.save_updated()

    # Clean up attachment files from disk
    task_upload_dir = UPLOADS_DIR / task_id
    if task_upload_dir.exists():
        shutil.rmtree(task_upload_dir, ignore_errors=True)

    await publish_event(project_id, "task.deleted", {"id": task_id})
    await _deindex_task(task_id)


@router.post("/{task_id}/complete")
async def complete_task(
    project_id: str, task_id: str, body: CompleteTaskRequest | None = None, user: User = Depends(get_current_user)
) -> dict:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.record_change("status", task.status, TaskStatus.done, str(user.id))
    task.status = TaskStatus.done
    task.completed_at = datetime.now(UTC)
    if body and body.completion_report is not None:
        task.completion_report = body.completion_report
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.post("/{task_id}/reopen")
async def reopen_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.record_change("status", task.status, TaskStatus.todo, str(user.id))
    task.status = TaskStatus.todo
    task.completed_at = None
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.post("/{task_id}/archive")
async def archive_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.archived = True
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.post("/{task_id}/unarchive")
async def unarchive_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.archived = False
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    await _index_task(task)
    return _task_dict(task)


@router.post("/{task_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    project_id: str, task_id: str, body: AddCommentRequest, user: User = Depends(get_current_user)
) -> dict:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    comment = Comment(content=body.content, author_id=str(user.id), author_name=user.name)
    task.comments.append(comment)
    await task.save_updated()
    await publish_event(project_id, "comment.added", {"task_id": task_id, "comment": {
        "id": comment.id, "content": comment.content,
        "author_id": comment.author_id, "author_name": comment.author_name,
        "created_at": comment.created_at.isoformat(),
    }})
    await _index_task(task)
    return _task_dict(task)


@router.delete("/{task_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    project_id: str, task_id: str, comment_id: str, user: User = Depends(get_current_user)
) -> None:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    comment = next((c for c in task.comments if c.id == comment_id), None)
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if comment.author_id != str(user.id) and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not comment author")

    task.comments = [c for c in task.comments if c.id != comment_id]
    await task.save_updated()


@router.post("/{task_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    project_id: str, task_id: str, file: UploadFile, user: User = Depends(get_current_user)
) -> dict:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Max size: {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    safe_filename = Path(file.filename).name if file.filename else "upload"
    unique_name = f"{uuid.uuid4().hex}_{safe_filename}"
    task_dir = UPLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    dest = task_dir / unique_name
    try:
        dest.write_bytes(contents)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=f"Failed to write file to disk: {exc}",
        )

    attachment = Attachment(
        filename=unique_name,
        content_type=file.content_type,
        size=len(contents),
    )
    task.attachments.append(attachment)
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return {
        "id": attachment.id,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size": attachment.size,
        "created_at": attachment.created_at.isoformat(),
    }


@router.delete("/{task_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    project_id: str, task_id: str, attachment_id: str, user: User = Depends(get_current_user)
) -> None:
    valid_object_id(task_id)
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    attachment = next((a for a in task.attachments if a.id == attachment_id), None)
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    # Delete file from disk
    file_path = UPLOADS_DIR / task_id / attachment.filename
    if file_path.exists():
        file_path.unlink()

    task.attachments = [a for a in task.attachments if a.id != attachment_id]
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
