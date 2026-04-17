"""Shared schemas, constants, and access helpers for task endpoint submodules."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from .....core.config import settings
from .....core.validators import valid_object_id
from .....models import Project, User
from .....models.task import TaskPriority, TaskStatus, TaskType

UPLOADS_DIR = Path(settings.UPLOADS_DIR)
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# ── Request / response schemas ───────────────────────────────


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
    active_form: str | None = Field(None, max_length=500)


class CompleteTaskRequest(BaseModel):
    completion_report: str | None = Field(None, max_length=10000)


class ReorderTasksRequest(BaseModel):
    task_ids: list[str] = Field(..., min_length=1, max_length=200)


class ExportTasksRequest(BaseModel):
    task_ids: list[str] = Field(..., min_length=1, max_length=50)
    format: str = Field("markdown", pattern=r"^(markdown|pdf)$")


class AddCommentRequest(BaseModel):
    content: str = Field(..., max_length=10000)


class BatchUpdateItem(BaseModel):
    task_id: str
    needs_detail: bool | None = None
    approved: bool | None = None
    archived: bool | None = None


class BatchUpdateRequest(BaseModel):
    updates: list[BatchUpdateItem] = Field(..., min_length=1)


# ── Access helpers ───────────────────────────────────────────


async def check_project_access(project_id: str, user: User) -> Project:
    from .....models.project import ProjectStatus as _ProjectStatus

    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project or project.status == _ProjectStatus.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return project


def check_not_locked(project: Project) -> None:
    if project.is_locked:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Project is locked",
        )
