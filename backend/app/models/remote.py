from datetime import UTC, datetime

from beanie import Document, Indexed
from pydantic import Field


class RemoteAgent(Document):
    """Registered remote agent (SSH/exec capable host).

    Renamed from ``TerminalAgent`` (the historical name reflected an
    early terminal-emulation prototype). The current implementation is
    a remote-exec agent connected via WebSocket — see
    ``backend/app/api/v1/endpoints/workspaces`` for the REST + WS surface.
    """

    name: str
    key_hash: Indexed(str, unique=True)  # type: ignore[valid-type]
    owner_id: str  # User ID who registered this agent
    hostname: str = ""
    os_type: str = ""  # "darwin", "linux", "windows"
    available_shells: list[str] = Field(default_factory=list)
    # ``is_online`` was a persisted flag used to serve "currently
    # connected" in admin UIs across process restarts. It was
    # inherently racy (multi-worker, multi-process) and is now derived
    # from ``agent_manager.is_connected(agent_id)`` — the authoritative
    # in-memory signal. ``last_seen_at`` is still persisted as a
    # diagnostic timestamp.
    last_seen_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Auto-update support
    agent_version: str | None = None  # Version string reported by the agent
    auto_update: bool = True
    update_channel: str = "stable"  # stable | beta | canary

    class Settings:
        name = "remote_agents"
        indexes = [
            [("owner_id", 1), ("created_at", -1)],
        ]


class RemoteExecLog(Document):
    """Audit trail for MCP-initiated remote operations.

    ``project_id`` identifies the project whose ``ProjectRemoteBinding``
    was used to route the operation. Renamed from the historical
    ``workspace_id`` (2026-04-08) when the standalone
    ``remote_workspaces`` collection was folded into ``Project.remote``.
    """

    project_id: str
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
            [("project_id", 1), ("created_at", -1)],
        ]


class AgentRelease(Document):
    """A published agent binary release available for self-update."""

    version: str  # semver string e.g. "0.2.0"
    os_type: str  # "win32" | "linux" | "darwin"
    arch: str = "x64"
    channel: str = "stable"  # "stable" | "beta" | "canary"
    storage_path: str  # Path under settings.AGENT_RELEASES_DIR
    sha256: str  # lowercase hex
    size_bytes: int
    release_notes: str = ""
    uploaded_by: str  # User ID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "agent_releases"
        indexes = [
            [("os_type", 1), ("channel", 1), ("created_at", -1)],
        ]
