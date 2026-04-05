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


class RemoteWorkspace(Document):
    """Links a project to a remote agent + directory."""

    agent_id: Indexed(str)  # type: ignore[valid-type]
    project_id: Indexed(str, unique=True)  # type: ignore[valid-type]  # 1 project = 1 workspace
    remote_path: str  # Absolute path on remote machine
    label: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "remote_workspaces"
        indexes = [
            [("agent_id", 1)],
        ]


class RemoteExecLog(Document):
    """Audit trail for MCP-initiated remote operations."""

    workspace_id: str
    agent_id: str
    operation: str  # "exec" | "read_file" | "write_file" | "list_dir"
    detail: str  # command string or file path
    exit_code: int | None = None
    stdout_len: int = 0
    stderr_len: int = 0
    duration_ms: int = 0
    error: str = ""
    mcp_key_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "remote_exec_logs"
        indexes = [
            [("agent_id", 1), ("created_at", -1)],
            [("workspace_id", 1), ("created_at", -1)],
        ]
