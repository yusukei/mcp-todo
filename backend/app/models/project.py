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


class ProjectRemoteBinding(BaseModel):
    """Bind a project to a remote agent + directory on that agent.

    Embedded in :class:`Project`. Replaces the historical separate
    ``remote_workspaces`` collection (removed 2026-04-08) — the 1:1
    relation is now expressed structurally instead of via a unique
    index on ``RemoteWorkspace.project_id``.
    """

    agent_id: str
    remote_path: str  # Absolute path on the remote machine
    label: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    # Optional binding to a remote agent + directory. Configured from the
    # project settings UI; consumed by MCP remote_* tools and Chat
    # sessions to resolve the execution host and cwd.
    remote: ProjectRemoteBinding | None = None
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
