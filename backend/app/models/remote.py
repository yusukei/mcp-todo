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
    # Stable identifier for "this physical host", reported by the agent
    # at auth-time. Lets the backend join an agent record with the
    # supervisor running on the same machine — see ``RemoteSupervisor``.
    # Empty string for legacy agents that predate the field.
    host_id: str = ""
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
    # User._id of the API key's owner at the time of the operation.
    # Persisted alongside ``mcp_key_id`` so the audit trail is
    # self-joinable: an operator can answer "who did this?" with a
    # single ``User`` lookup instead of the historical
    # ``McpApiKey._id`` → ``McpApiKey.created_by`` → ``User._id``
    # two-hop chain. Empty string for legacy records written before
    # this field existed and for denied attempts where authentication
    # failed before the key owner could be resolved.
    mcp_key_owner_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "remote_exec_logs"
        indexes = [
            [("agent_id", 1), ("created_at", -1)],
            [("project_id", 1), ("created_at", -1)],
            [("mcp_key_owner_id", 1), ("created_at", -1)],
        ]


class RemoteSupervisor(Document):
    """Registered Rust supervisor managing a remote agent process.

    A supervisor lives on the same host as exactly one agent; it owns
    the agent's OS process (spawn / restart / upgrade / log capture)
    and exposes that surface to operators via the ``supervisor_*``
    MCP tools. The supervisor's WebSocket is a separate channel from
    the agent's — they share neither auth token nor envelope namespace.

    Authorization: ``sv_`` tokens grant restart / upgrade / config
    reload; treat them as more sensitive than ``ta_`` agent tokens.
    See the Rust Supervisor design doc §3.4 (Token Lifecycle) for the
    rotation / revocation flow.
    """

    name: str
    key_hash: Indexed(str, unique=True)  # type: ignore[valid-type]
    owner_id: str  # User ID who registered this supervisor
    # Same value the agent on this host reports in ``RemoteAgent.host_id``.
    # Joining on ``host_id`` lets the UI display "supervisor X manages
    # agent Y" without an explicit foreign key.
    host_id: str = ""
    hostname: str = ""
    os_type: str = ""
    last_seen_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Versions reported via ``supervisor_info`` push.
    supervisor_version: str | None = None
    agent_version: str | None = None  # version of the agent the supervisor is currently running
    agent_pid: int | None = None
    agent_uptime_s: int | None = None

    class Settings:
        name = "remote_supervisors"
        indexes = [
            [("owner_id", 1), ("created_at", -1)],
            [("host_id", 1)],
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
