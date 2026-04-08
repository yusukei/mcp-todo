from datetime import UTC, datetime
from enum import StrEnum as str_enum

from beanie import Document, Indexed, Link
from pydantic import BaseModel, Field

from .user import User


class ProjectStatus(str_enum):
    active = "active"
    archived = "archived"


class MemberRole(str_enum):
    owner = "owner"
    member = "member"


class ProjectMember(BaseModel):
    user_id: str
    role: MemberRole = MemberRole.member
    joined_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Project(Document):
    name: Indexed(str)
    description: str = ""
    color: str = "#6366f1"
    status: ProjectStatus = ProjectStatus.active
    is_locked: bool = False
    sort_order: int = 0
    # Hidden from the main project list / sidebar. Used by the singleton
    # "Common" project that hosts cross-cutting features like Chat and
    # Bookmarks without bloating the visible project list.
    hidden: bool = False
    members: list[ProjectMember] = Field(default_factory=list)
    created_by: Link[User]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "projects"

    def has_member(self, user_id: str) -> bool:
        return any(m.user_id == user_id for m in self.members)

    def is_owner(self, user_id: str) -> bool:
        return any(
            m.user_id == user_id and m.role == MemberRole.owner for m in self.members
        )

    def get_member(self, user_id: str) -> ProjectMember | None:
        return next((m for m in self.members if m.user_id == user_id), None)

    async def save_updated(self) -> "Project":
        self.updated_at = datetime.now(UTC)
        await self.save()
        return self
