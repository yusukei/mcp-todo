"""Registry of remote-agent RPC operations.

The MCP layer in :mod:`app.mcp.tools.remote` used to call
``_send_to_agent(binding, "exec", payload, timeout=60, operation="exec", ...)``
from eleven different tools. The ``msg_type`` literal, the per-tool
default timeout, and the audit-log operation name were all repeated
at every call site, which made:

- typos in ``msg_type`` silent runtime errors (caught only when the
  agent returned an "unknown handler" reply),
- default timeouts impossible to tune in one place,
- adding a new remote tool a "remember to update three things"
  exercise.

The registry below is the single source of truth. ``_send_to_agent``
looks up the spec, fills in the default timeout when the caller does
not pass an override, and uses the spec's audit-log label so the
caller no longer has to repeat it.

The agent side has its own dispatch table (``agent.main._HANDLERS``);
the registries are kept in sync **by convention**, not by import,
because the agent ships independently of the backend and may be on a
slightly older or newer version. The backend's registry is therefore
the *minimum* contract — the agent must support every ``msg_type``
listed here, but is allowed to support additional ones that are not
yet in this registry (a new agent talking to an old backend).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteToolSpec:
    """Static description of a single remote-agent RPC.

    Attributes:
        msg_type: Wire-level ``type`` field sent to the agent.
        default_timeout: Per-call timeout in seconds. Callers may
            override via the ``timeout=`` argument to
            ``_send_to_agent`` (e.g. ``remote_exec`` adds a small
            buffer on top of the user-supplied execution timeout).
        audit_op: Label used in :class:`RemoteExecLog` records.
            Defaults to ``msg_type`` when ``None`` — only set this
            if the audit label needs to differ from the wire name.
    """

    msg_type: str
    default_timeout: float
    audit_op: str | None = None

    @property
    def operation_label(self) -> str:
        return self.audit_op or self.msg_type


# Registry — the single source of truth for backend-supported remote
# operations. Adding a new remote tool means adding one entry here.
REMOTE_TOOLS: dict[str, RemoteToolSpec] = {
    "exec":       RemoteToolSpec("exec",       default_timeout=60),
    "read_file":  RemoteToolSpec("read_file",  default_timeout=30),
    "write_file": RemoteToolSpec("write_file", default_timeout=30),
    "list_dir":   RemoteToolSpec("list_dir",   default_timeout=15),
    "stat":       RemoteToolSpec("stat",       default_timeout=10),
    "mkdir":      RemoteToolSpec("mkdir",      default_timeout=10),
    "delete":     RemoteToolSpec("delete",     default_timeout=30),
    "move":       RemoteToolSpec("move",       default_timeout=30),
    "copy":       RemoteToolSpec("copy",       default_timeout=60),
    "glob":       RemoteToolSpec("glob",       default_timeout=30),
    "grep":       RemoteToolSpec("grep",       default_timeout=60),
}


def get_spec(msg_type: str) -> RemoteToolSpec | None:
    """Look up a tool spec by ``msg_type``. Returns ``None`` for unknowns."""
    return REMOTE_TOOLS.get(msg_type)
