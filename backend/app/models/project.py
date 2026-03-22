from datetime import UTC, datetime
from enum import StrEnum as str_enum

from beanie import Document, Indexed, Link
from pydantic import BaseModel, Field

from .user import User


class ProjectStatus(str_enum):
    active = "active"
    archived = "archived"


class ProjectMember(BaseModel):
    user_id: str
    joined_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Project(Document):
    name: Indexed(str)
    description: str = ""
    color: str = "#6366f1"
    status: ProjectStatus = ProjectStatus.active
    members: list[ProjectMember] = Field(default_factory=list)
    created_by: Link[User]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "projects"

    def has_member(self, user_id: str) -> bool:
        return any(m.user_id == user_id for m in self.members)

    async def save_updated(self) -> "Project":
        self.updated_at = datetime.now(UTC)
        await self.save()
        return self
