from datetime import UTC, datetime

from beanie import Document, Indexed
from pydantic import Field


class TerminalAgent(Document):
    """Registered remote terminal agent."""

    name: str
    key_hash: Indexed(str, unique=True)  # type: ignore[valid-type]
    owner_id: str  # User ID who registered this agent
    hostname: str = ""
    os_type: str = ""  # "darwin", "linux", "windows"
    available_shells: list[str] = Field(default_factory=list)
    is_online: bool = False
    last_seen_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "terminal_agents"
        indexes = [
            [("owner_id", 1), ("created_at", -1)],
        ]


class TerminalSession(Document):
    """Audit log for terminal sessions."""

    agent_id: str
    user_id: str
    shell: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None

    class Settings:
        name = "terminal_sessions"
        indexes = [
            [("agent_id", 1), ("started_at", -1)],
            [("user_id", 1), ("started_at", -1)],
        ]
