from datetime import UTC, datetime
from enum import StrEnum as str_enum

from beanie import Document, Indexed
from bson import ObjectId
from pydantic import BaseModel, Field


class TaskStatus(str_enum):
    todo = "todo"
    in_progress = "in_progress"
    in_review = "in_review"
    done = "done"
    cancelled = "cancelled"


class TaskPriority(str_enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class Comment(BaseModel):
    id: str = Field(default_factory=lambda: str(ObjectId()))
    content: str
    author_id: str
    author_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Task(Document):
    project_id: Indexed(str)
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.medium
    due_date: datetime | None = None
    assignee_id: str | None = None
    parent_task_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    created_by: str
    completed_at: datetime | None = None
    is_deleted: bool = False
    archived: bool = False
    needs_detail: bool = False
    approved: bool = False
    sort_order: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "tasks"
        indexes = [
            [("project_id", 1), ("is_deleted", 1), ("status", 1)],
            [("assignee_id", 1), ("is_deleted", 1)],
            [("is_deleted", 1), ("status", 1), ("due_date", 1)],
            [("parent_task_id", 1), ("is_deleted", 1)],
            [("due_date", 1), ("status", 1), ("is_deleted", 1)],
        ]

    async def save_updated(self) -> "Task":
        self.updated_at = datetime.now(UTC)
        await self.save()
        return self
