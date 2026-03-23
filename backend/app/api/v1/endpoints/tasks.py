import asyncio
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Project, Task, User
from ....models.task import Attachment, Comment, TaskPriority, TaskStatus
from ....services.events import publish_event
from ....services.serializers import task_to_dict as _task_dict

UPLOADS_DIR = Path(__file__).resolve().parents[4] / "uploads"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    title: str = Field(..., max_length=255)
    description: str = Field("", max_length=10000)
    priority: TaskPriority = TaskPriority.medium
    status: TaskStatus = TaskStatus.todo
    due_date: datetime | None = None
    assignee_id: str | None = None
    parent_task_id: str | None = None
    tags: list[str] = []


class UpdateTaskRequest(BaseModel):
    title: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=10000)
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: datetime | None = None
    assignee_id: str | None = None
    tags: list[str] | None = None
    needs_detail: bool | None = None
    approved: bool | None = None
    completion_report: str | None = Field(None, max_length=10000)


class CompleteTaskRequest(BaseModel):
    completion_report: str | None = Field(None, max_length=10000)


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


@router.get("")
async def list_tasks(
    project_id: str,
    task_status: TaskStatus | None = Query(None, alias="status"),
    priority: TaskPriority | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
    needs_detail: bool | None = None,
    approved: bool | None = None,
    archived: bool | None = None,
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
    if needs_detail is not None:
        query = query.find(Task.needs_detail == needs_detail)
    if approved is not None:
        query = query.find(Task.approved == approved)
    if archived is not None:
        query = query.find(Task.archived == archived)

    total, tasks = await asyncio.gather(
        query.count(),
        query.clone().sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list(),
    )
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    project_id: str, body: CreateTaskRequest, user: User = Depends(get_current_user)
) -> dict:
    await _check_project_access(project_id, user)

    task = Task(
        project_id=project_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        status=body.status,
        due_date=body.due_date,
        assignee_id=body.assignee_id,
        parent_task_id=body.parent_task_id,
        tags=body.tags,
        created_by=str(user.id),
    )
    await task.insert()
    await publish_event(project_id, "task.created", _task_dict(task))
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
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    # Use exclude_unset to distinguish "not sent" from "sent as null".
    # This allows clients to clear fields like due_date and assignee_id by
    # sending null explicitly.
    updates = body.model_dump(exclude_unset=True)

    if "title" in updates:
        task.title = updates["title"]
    if "description" in updates:
        task.description = updates["description"]
    if "priority" in updates:
        task.priority = updates["priority"]
    if "status" in updates:
        task.transition_status(updates["status"])
    if "due_date" in updates:
        task.due_date = updates["due_date"]
    if "assignee_id" in updates:
        task.assignee_id = updates["assignee_id"]
    if "tags" in updates:
        task.tags = updates["tags"]
    if "needs_detail" in updates:
        task.needs_detail = updates["needs_detail"]
        if updates["needs_detail"]:
            task.approved = False
    if "approved" in updates:
        task.approved = updates["approved"]
        if updates["approved"]:
            task.needs_detail = False
    if "completion_report" in updates:
        task.completion_report = updates["completion_report"]

    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> None:
    valid_object_id(task_id)
    await _check_project_access(project_id, user)
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


@router.post("/{task_id}/complete")
async def complete_task(
    project_id: str, task_id: str, body: CompleteTaskRequest | None = None, user: User = Depends(get_current_user)
) -> dict:
    valid_object_id(task_id)
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.status = TaskStatus.done
    task.completed_at = datetime.now(UTC)
    if body and body.completion_report is not None:
        task.completion_report = body.completion_report
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.post("/{task_id}/reopen")
async def reopen_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(task_id)
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.status = TaskStatus.todo
    task.completed_at = None
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.post("/{task_id}/archive")
async def archive_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(task_id)
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.archived = True
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.post("/{task_id}/unarchive")
async def unarchive_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(task_id)
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.archived = False
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.post("/{task_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    project_id: str, task_id: str, body: AddCommentRequest, user: User = Depends(get_current_user)
) -> dict:
    valid_object_id(task_id)
    await _check_project_access(project_id, user)
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
    return _task_dict(task)


@router.delete("/{task_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    project_id: str, task_id: str, comment_id: str, user: User = Depends(get_current_user)
) -> None:
    valid_object_id(task_id)
    await _check_project_access(project_id, user)
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
    await _check_project_access(project_id, user)
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
    await _check_project_access(project_id, user)
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
