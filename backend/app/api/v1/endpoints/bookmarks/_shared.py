"""Shared schemas + project-access helpers for bookmark endpoints."""
from __future__ import annotations

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from .....core.validators import valid_object_id
from .....models import Project, User


class CreateBookmarkRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    title: str = Field("", max_length=255)
    description: str = Field("", max_length=10000)
    tags: list[str] = Field(default_factory=list)
    collection_id: str | None = None


class UpdateBookmarkRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    tags: list[str] | None = None
    collection_id: str | None = None
    is_starred: bool | None = None


class CreateCollectionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=10000)
    icon: str = Field("folder", max_length=50)
    color: str = Field("#6366f1", max_length=20)


class UpdateCollectionRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    icon: str | None = Field(None, max_length=50)
    color: str | None = Field(None, max_length=20)


class BatchBookmarkAction(BaseModel):
    bookmark_ids: list[str] = Field(..., min_length=1)
    action: str  # "delete" | "star" | "unstar" | "set_collection" | "add_tags" | "remove_tags"
    collection_id: str | None = None
    tags: list[str] | None = None


class ReorderRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1)


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
        raise HTTPException(status.HTTP_423_LOCKED, "Project is locked")
