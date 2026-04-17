from datetime import UTC, datetime
from enum import StrEnum as str_enum

from beanie import Document, Indexed
from bson import ObjectId
from pydantic import BaseModel, Field


class TaskStatus(str_enum):
    todo = "todo"
    in_progress = "in_progress"
    on_hold = "on_hold"
    done = "done"
    cancelled = "cancelled"


class TaskPriority(str_enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskType(str_enum):
    action = "action"
    decision = "decision"


class DecisionOption(BaseModel):
    label: str
    description: str = ""


class DecisionContext(BaseModel):
    background: str = ""
    decision_point: str = ""
    options: list[DecisionOption] = Field(default_factory=list)
    recommendation: str | None = None


class ActivityEntry(BaseModel):
    field: str
    old_value: str | None = None
    new_value: str | None = None
    changed_by: str = ""
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Attachment(BaseModel):
    id: str = Field(default_factory=lambda: str(ObjectId()))
    filename: str
    content_type: str
    size: int  # bytes
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    task_type: TaskType = TaskType.action
    decision_context: DecisionContext | None = None
    tags: list[str] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    activity_log: list[ActivityEntry] = Field(default_factory=list)
    created_by: str
    completion_report: str | None = None
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
            # User task searches (e.g. "my tasks" filtered by deletion state)
            [("created_by", 1), ("is_deleted", 1)],
            # Subtask retrieval scoped to a project
            [("project_id", 1), ("parent_task_id", 1)],
            # get_work_context: approved tasks query
            [("is_deleted", 1), ("approved", 1), ("status", 1)],
            # get_work_context: needs_detail tasks query
            [("is_deleted", 1), ("needs_detail", 1), ("status", 1)],
            # Sorted task listings within a project
            [("project_id", 1), ("is_deleted", 1), ("sort_order", 1)],
            # Cross-task dependencies (Sprint 1)
            [("project_id", 1), ("blocked_by", 1)],
            [("project_id", 1), ("blocks", 1)],
        ]

    def record_change(self, field: str, old_value: str | None, new_value: str | None, changed_by: str = "") -> None:
        """Append an activity log entry for a field change."""
        if old_value == new_value:
            return
        self.activity_log.append(ActivityEntry(
            field=field,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        ))

    def transition_status(self, new_status: "TaskStatus") -> None:
        """Update status and manage completed_at timestamp accordingly."""
        if new_status == TaskStatus.done and self.status != TaskStatus.done:
            self.completed_at = datetime.now(UTC)
        elif new_status != TaskStatus.done:
            self.completed_at = None
        self.status = new_status

    async def save_updated(self) -> "Task":
        self.updated_at = datetime.now(UTC)
        await self.save()
        return self
