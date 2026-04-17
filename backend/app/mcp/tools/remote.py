"""MCP tools for remote command execution and file operations via connected agents."""

import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from fastmcp.exceptions import ToolError

from ...core.config import settings
from ...models import Project
from ...models.remote import RemoteAgent, RemoteExecLog
from ...services.remote_tool_spec import REMOTE_TOOLS


@dataclass(frozen=True)
class ResolvedBinding:
    """Resolved (project → agent → remote_path) triple for a remote operation.

    Materialised from the ``Project.remote`` embedded field by
    :func:`_resolve_binding`. Kept as a lightweight dataclass so the
    per-tool call sites don't have to know about the Project document.
    """

    project_id: str
    agent_id: str
    remote_path: str
from ...services.agent_manager import (
    AgentOfflineError,
    CommandTimeoutError,
    agent_manager,
)
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _resolve_project_id

logger = logging.getLogger(__name__)

# Per-call constants kept inline because they describe protocol-level
# byte limits that are not operator-tunable; promoting them to Settings
# would imply a knob we do not actually support.
MAX_COMMAND_BYTES = 8 * 1024  # 8 KB — upper bound for a single shell command
MAX_PATTERN_BYTES = 4 * 1024  # 4 KB — upper bound for grep/glob patterns


# ── Env override denylist (Security H-2) ─────────────────────
#
# The ``env`` dict on ``remote_exec`` is merged into the agent
# subprocess's environment. Several env vars let an attacker hijack
# the interpreter search path or inject shared libraries — those must
# never be settable over an MCP call regardless of who the caller is.
#
# The denylist is deliberately keyed on the **exact** variable name
# plus a small prefix list. Prefix matching is case-insensitive so
# a caller cannot evade the check by submitting ``ld_preload``.
_ENV_DENY_EXACT = frozenset(
    x.upper() for x in (
        "PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
        "PYTHONEXECUTABLE", "PYTHONUSERBASE", "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE", "PYTHONVERBOSE", "PYTHONWARNINGS",
        "PYTHONBREAKPOINT", "PYTHONINSPECT", "PYTHONMALLOC",
        "NODE_OPTIONS", "NODE_PATH",
        "CLASSPATH", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
        "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB",
        "GEM_PATH", "GEM_HOME", "BUNDLE_PATH",
    )
)
_ENV_DENY_PREFIXES = (
    "LD_", "DYLD_",  # dynamic linker injection (glibc / mach-o)
    "LIBRARY_PATH",
)


def _validate_remote_env(env: dict[str, str]) -> None:
    """Reject env vars that could hijack the agent subprocess.

    Raises :class:`ToolError` on the first rejected key so the denied
    audit wrapper (`_audit_on_denied`) can record the attempt. Callers
    must still type-check ``env`` values beforehand.
    """
    for key in env:
        upper = key.upper()
        if upper in _ENV_DENY_EXACT:
            raise ToolError(
                f"env key {key!r} is denied for remote_exec (runtime hijack risk)"
            )
        for prefix in _ENV_DENY_PREFIXES:
            if upper.startswith(prefix):
                raise ToolError(
                    f"env key {key!r} is denied for remote_exec "
                    f"(matches denied prefix {prefix})"
                )


# ── Secret masking for audit logs (Security H-4) ─────────────
#
# RemoteExecLog.detail stores command strings and file paths. Without
# masking, tokens pasted into ``remote_exec`` (e.g. ``curl -H "Bearer
# sk-..."``, ``AWS_SECRET=... ./script``) would sit in the audit
# collection indefinitely. These patterns cover the common leak
# shapes; they are intentionally conservative (false positives just
# mean an extra ``***`` in the log, which is harmless).
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # --token=VALUE / --password VALUE / -p VALUE — quoted or bare
    (
        re.compile(
            r"(--?(?:token|password|passwd|pwd|secret|api[-_]?key|auth|"
            r"access[-_]?key|session[-_]?token)[= ])(\"[^\"]*\"|'[^']*'|\S+)",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
    # Authorization: Bearer xxx / Basic xxx
    (
        re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9._~/+=\-]+"),
        r"\1 ***",
    ),
    # FOO_TOKEN=bar / FOO_SECRET=bar / FOO_KEY=bar / FOO_PASSWORD=bar
    (
        re.compile(
            r"([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASSWD|APIKEY)\s*=\s*)"
            r"(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1***",
    ),
)


def _mask_secrets(text: str) -> str:
    """Mask common secret shapes in an audit-log string.

    Non-destructive: returns ``text`` unchanged if no pattern matches.
    Applied by :func:`_log_operation` and :func:`_log_denied` before
    persisting any caller-supplied ``detail`` or ``error`` string.
    """
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _validate_remote_path(path: str) -> str:
    """Defense-in-depth path validation at the MCP layer.

    Rejects ``..`` segments, NUL bytes, and control characters before the
    request is sent to the agent. The agent itself ALSO validates the path
    (`_resolve_safe_path`); this is the second line of defense.
    """
    if not isinstance(path, str):
        raise ToolError("path must be a string")
    if "\x00" in path or "\r" in path or "\n" in path:
        raise ToolError("Invalid characters in path")
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if any(part == ".." for part in parts):
        raise ToolError("Path traversal not allowed (.. segment)")
    return path


def _validate_remote_command(command: str) -> str:
    """Input-sanity check for shell commands sent to the agent.

    NOTE: this is **not** a security boundary. The agent ultimately runs
    the string through a shell, so meta-characters like ``$()``, backticks,
    ``|``, ``;``, ``&&``, ``>``, glob, etc. are intentionally allowed —
    blocking them would break legitimate use (``git log | head``,
    ``cd x && pytest``, …). The real security boundary for ``remote_exec``
    is authentication + Project.members access control + workspace path
    isolation, enforced upstream by ``authenticate()`` / ``_resolve_binding``
    and on the agent side by its own path/exec safeguards.

    What this function actually does:

    - rejects NUL bytes and raw CR/LF (multi-line scripts must be joined
      with ``;`` / ``&&`` so the agent receives a single logical command),
    - rejects empty / whitespace-only commands (caller bug),
    - caps total length so a single MCP call cannot ship megabyte-sized
      payloads through the WebSocket pipe.

    Treat this as a sanity guard against caller mistakes and accidentally
    pasted control characters — never as a substitute for the real
    authorization layer.
    """
    if not isinstance(command, str):
        raise ToolError("command must be a string")
    if not command.strip():
        raise ToolError("command must not be empty")
    if "\x00" in command:
        raise ToolError("Invalid character in command (NUL byte)")
    if "\r" in command or "\n" in command:
        raise ToolError("Invalid character in command (CR/LF — join with ; or &&)")
    if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ToolError(
            f"Command too long (max {MAX_COMMAND_BYTES} bytes)"
        )
    return command


def _validate_remote_pattern(pattern: str, *, kind: str) -> str:
    """Sanity-check grep/glob patterns before sending to the agent.

    Same spirit as ``_validate_remote_command``: this is **not** a security
    boundary, just a guard against caller mistakes and runaway payloads.

    - rejects NUL bytes and CR/LF (a literal newline in a pattern is
      almost always a bug — the user accidentally pasted multi-line input;
      ripgrep's multi-line mode is not exposed via this tool),
    - rejects empty / whitespace-only patterns,
    - caps total length so a megabyte-sized regex cannot be shipped
      through the WebSocket pipe (and cannot be compiled by the Python
      fallback engine — a slow-compile vector even before runtime
      backtracking).

    NOTE on ReDoS: this function does **not** detect catastrophic
    backtracking. The agent's grep handler is responsible for bounding
    execution time (it already runs under ripgrep when available, which
    is linear-time, and falls back to Python ``re`` otherwise). A future
    hardening pass could add an explicit per-call regex timeout on the
    agent side; flagging here for visibility.
    """
    if not isinstance(pattern, str):
        raise ToolError(f"{kind} pattern must be a string")
    if not pattern.strip():
        raise ToolError(f"{kind} pattern must not be empty")
    if "\x00" in pattern:
        raise ToolError(f"Invalid character in {kind} pattern (NUL byte)")
    if "\r" in pattern or "\n" in pattern:
        raise ToolError(f"Invalid character in {kind} pattern (CR/LF)")
    if len(pattern.encode("utf-8")) > MAX_PATTERN_BYTES:
        raise ToolError(
            f"{kind} pattern too long (max {MAX_PATTERN_BYTES} bytes)"
        )
    return pattern


async def _resolve_binding(project_id: str, key_info: dict) -> ResolvedBinding:
    """Resolve project_id to a :class:`ResolvedBinding` with access checks.

    Reads the embedded ``Project.remote`` field. Raises ``ToolError`` if
    the project has no remote binding configured.
    """
    project_id = await _resolve_project_id(project_id)
    project = await check_project_access(project_id, key_info)
    if not project.remote:
        raise ToolError(f"No remote agent bound to project {project_id}")
    return ResolvedBinding(
        project_id=str(project.id),
        agent_id=project.remote.agent_id,
        remote_path=project.remote.remote_path,
    )


async def _log_operation(
    binding: ResolvedBinding,
    operation: str,
    detail: str,
    key_info: dict,
    duration_ms: int = 0,
    exit_code: int | None = None,
    stdout_len: int = 0,
    stderr_len: int = 0,
    error: str = "",
) -> None:
    """Record operation to audit log.

    Per CLAUDE.md "No error hiding": DB write failures are **not**
    swallowed. An audit record dropped on the floor is a critical
    event — operators need to see it. If ``log.insert()`` raises,
    the exception propagates to the MCP tool caller, which is the
    correct boundary to convert it into a protocol-level error.

    ``key_info`` is the dict returned by :func:`authenticate`. We
    persist both ``key_id`` and ``user_id`` so the audit trail is
    self-joinable to a single ``User`` lookup — see
    ``RemoteExecLog.mcp_key_owner_id`` for the rationale.
    """
    log = RemoteExecLog(
        project_id=binding.project_id,
        agent_id=binding.agent_id,
        operation=operation,
        detail=_mask_secrets(detail)[:500],
        exit_code=exit_code,
        stdout_len=stdout_len,
        stderr_len=stderr_len,
        duration_ms=duration_ms,
        error=_mask_secrets(error)[:500],
        mcp_key_id=key_info.get("key_id", ""),
        mcp_key_owner_id=key_info.get("user_id", ""),
    )
    await log.insert()


async def _log_denied(
    *,
    operation: str,
    project_id: str,
    agent_id: str,
    key_info: dict | None,
    detail: str,
    reason: str,
) -> None:
    """Record a denied/rejected attempt to the audit log.

    Used for authentication, authorization, and input-validation
    failures that happen **before** the request is dispatched to the
    agent. Recording these attempts is a Security H-3 requirement —
    without them, attack probes against ``remote_*`` tools leave no
    trace in the audit trail.

    Partial information is expected: ``project_id``/``agent_id``/
    ``key_info`` may all be missing when the failure occurred before
    the binding was resolved or before authentication completed.
    Exceptions from the DB insert propagate — losing an audit record
    of a rejected attempt is a critical event.
    """
    log = RemoteExecLog(
        project_id=project_id or "",
        agent_id=agent_id or "",
        operation=operation,
        detail=_mask_secrets(detail or "")[:500],
        error=_mask_secrets(f"denied: {reason}")[:500],
        mcp_key_id=(key_info.get("key_id", "") if key_info else ""),
        mcp_key_owner_id=(key_info.get("user_id", "") if key_info else ""),
    )
    await log.insert()


class _PreflightCtx:
    """Mutable slot passed into the :func:`_audit_on_denied` context.

    The tool body assigns ``key_info`` and ``binding`` as each step
    completes; on an exception the context manager uses whatever was
    populated so the denied audit entry still carries as much
    information as possible.
    """

    __slots__ = ("key_info", "binding")

    def __init__(self) -> None:
        self.key_info: dict[str, Any] | None = None
        self.binding: ResolvedBinding | None = None


@asynccontextmanager
async def _audit_on_denied(operation: str, project_id: str, detail: str = ""):
    """Async context that records a denied audit entry on any exception.

    Wrap the authenticate → resolve_binding → validate prelude of each
    MCP tool in this context. If anything in the ``with`` body raises,
    the denial is persisted to :class:`RemoteExecLog` with whatever
    identifiers have been populated on the yielded :class:`_PreflightCtx`
    so far, and the exception is re-raised unchanged.

    Scope: only covers pre-execution validation. Operational failures
    raised by :func:`_send_to_agent` (AgentOfflineError, timeout, …)
    are logged separately via :func:`_log_operation` on their
    dedicated error paths and must NOT be wrapped in this context to
    avoid double-logging.
    """
    ctx = _PreflightCtx()
    try:
        yield ctx
    except Exception as exc:
        logger.warning(
            "[audit] denied %s project_id=%s: %s",
            operation, project_id, exc, exc_info=exc,
        )
        await _log_denied(
            operation=operation,
            project_id=(ctx.binding.project_id if ctx.binding else (project_id or "")),
            agent_id=(ctx.binding.agent_id if ctx.binding else ""),
            key_info=ctx.key_info,
            detail=detail,
            reason=f"{type(exc).__name__}: {exc}",
        )
        raise


async def _send_to_agent(
    binding: ResolvedBinding,
    msg_type: str,
    payload: dict,
    *,
    detail: str,
    key_info: dict,
    timeout: float | None = None,
) -> dict:
    """Common request/response wrapper with audit logging + agent wait.

    Looks up ``msg_type`` in the :data:`REMOTE_TOOLS` registry to
    derive the default timeout and audit-log label, then forwards to
    :meth:`agent_manager.send_request`. Callers can override the
    default timeout via the ``timeout`` keyword (used by
    ``remote_exec`` to add a small buffer on top of the user-supplied
    execution timeout).

    Centralizes the AgentOfflineError / CommandTimeoutError /
    RuntimeError handling so individual MCP tools stay short.
    """
    spec = REMOTE_TOOLS.get(msg_type)
    if spec is None:
        # Loud failure: an MCP tool tried to dispatch an op that the
        # backend does not know how to describe. This is a backend
        # bug, not a runtime error from the agent — surface it as
        # ToolError so it lands in the operator's logs.
        raise ToolError(f"Unknown remote tool msg_type: {msg_type!r}")
    effective_timeout = timeout if timeout is not None else spec.default_timeout
    operation = spec.operation_label

    t0 = time.monotonic()
    try:
        result = await agent_manager.send_request(
            binding.agent_id,
            msg_type,
            payload,
            timeout=effective_timeout,
            wait_for_agent=settings.REMOTE_DEFAULT_AGENT_WAIT_SECONDS,
        )
    except AgentOfflineError:
        await _log_operation(binding, operation, detail, key_info, error="agent_offline")
        raise ToolError("Agent is offline")
    except CommandTimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _log_operation(binding, operation, detail, key_info,
                             duration_ms=duration_ms, error="timeout")
        raise ToolError(f"Request timed out after {effective_timeout}s")
    except RuntimeError as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _log_operation(binding, operation, detail, key_info,
                             duration_ms=duration_ms, error=str(e))
        raise ToolError(str(e))
    return result


@mcp.tool()
async def list_remote_agents() -> list[dict]:
    """List registered remote agents and their connection status.

    Returns a list of agents with id, name, hostname, os_type, is_online,
    the number of projects bound to the agent, and shell information
    (``available_shells`` reported by the agent + ``default_shell``
    derived from ``os_type`` when the agent hasn't reported one).
    """
    await authenticate()

    agents = await RemoteAgent.find_all().to_list()
    result = []
    for a in agents:
        aid = str(a.id)
        project_count = await Project.find({"remote.agent_id": aid}).count()
        available = list(a.available_shells or [])
        result.append({
            "id": aid,
            "name": a.name,
            "hostname": a.hostname,
            "os_type": a.os_type,
            "is_online": await agent_manager.is_connected_anywhere(aid),
            "project_count": project_count,
            "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
            "agent_version": a.agent_version,
            "auto_update": a.auto_update,
            "update_channel": a.update_channel,
            "available_shells": available,
            "default_shell": _derive_default_shell(a.os_type, available),
        })
    return result


def _derive_default_shell(os_type: str, available_shells: list[str]) -> str:
    """Pick a sensible default shell for the agent.

    The agent reports shells as absolute paths (e.g.
    ``C:\\Windows\\system32\\cmd.exe``); we match on the basename stem
    so the policy works regardless of install location.

    Prefers an explicitly reported POSIX shell when present, else falls
    back to the platform-native default. Kept conservative so existing
    ``remote_exec`` callers see the same shell they've always used.
    """
    import os as _os

    stems = {
        _os.path.splitext(_os.path.basename(s))[0].lower()
        for s in available_shells
        if isinstance(s, str) and s
    }
    for preferred in ("bash", "zsh", "sh"):
        if preferred in stems:
            return preferred
    if os_type in ("win32", "windows"):
        # Windows native default — cmd remains the interop-safe choice
        # for every installation, with or without Git/msys2/WSL.
        return "cmd"
    # POSIX hosts effectively always have ``sh``.
    return "sh"


@mcp.tool()
async def remote_exec(
    project_id: str,
    command: str,
    timeout: int = 60,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    inject_secrets: bool = False,
    run_in_background: bool | None = None,
    format: str = "text",
    max_bytes: int | None = None,
) -> dict | str:
    """Execute a shell command on the remote machine for this project.

    Runs in the project's remote directory. Supports any shell command
    (git, docker, npm, etc). See docs/mcp-tools/remote.md for details.

    Args:
        project_id: Project ID or name
        command: Shell command to execute
        timeout: Seconds (1-3600, default 60)
        cwd: Subdirectory inside the workspace (no traversal)
        env: Extra env vars merged with the agent's env
        inject_secrets: Merge all project secrets into env. Values never
            appear in LLM context or response. Preferred for credentials.
        run_in_background: Start in background, return ``job_id``
            immediately. Poll with ``remote_exec_status``. Always returns
            a dict regardless of ``format``.
        format: ``"text"`` (default, bash-like plain text with
            ``[stderr]``/``[exit N]`` markers) or ``"json"``.

    Returns:
        ``format="text"`` → ``str``: stdout plus ``[stderr]``/``[exit N]``/
        ``[... truncated ...]`` markers as needed.

        ``format="json"`` → dict with ``exit_code``, ``stdout``, ``stderr``,
        ``duration_ms``, and ``stdout_truncated``/``stderr_truncated`` +
        ``*_total_bytes`` to detect output >2MB agent buffer.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    async with _audit_on_denied("exec", project_id, detail=str(command)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_command(command)
        if cwd is not None:
            _validate_remote_path(cwd)
        if env is not None:
            if not isinstance(env, dict):
                raise ToolError("env must be a dict of string→string")
            for k, v in env.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ToolError("env keys and values must be strings")
            _validate_remote_env(env)
    key_info = audit.key_info
    binding = audit.binding

    # ── inject_secrets: merge project secrets into env ────────────
    if inject_secrets:
        from ...models.secret import ProjectSecret, SecretAccessLog

        secrets = await ProjectSecret.find(
            ProjectSecret.project_id == binding.project_id,
        ).to_list()
        if secrets:
            secret_env = {s.key: s.value for s in secrets}
            if env is not None:
                # Explicit env takes precedence over secrets
                secret_env.update(env)
            env = secret_env
            _validate_remote_env(env)
            # Audit log
            for s in secrets:
                await SecretAccessLog(
                    project_id=binding.project_id,
                    secret_key=s.key,
                    operation="inject",
                    user_id=key_info.get("user_id", ""),
                    auth_kind=key_info.get("auth_kind", ""),
                ).insert()

    timeout = max(1, min(timeout, settings.REMOTE_MAX_TIMEOUT_SECONDS))
    payload: dict = {
        "command": command,
        "cwd": binding.remote_path,
        "timeout": timeout,
    }
    if cwd is not None:
        payload["cwd_override"] = cwd
    if env is not None:
        payload["env"] = env

    # Background execution: start and return job_id immediately
    if run_in_background:
        result = await _send_to_agent(
            binding, "exec_background", payload,
            detail=command, key_info=key_info, timeout=30,
        )
        await _log_operation(
            binding, "exec_background", command, key_info,
        )
        return {
            "job_id": result.get("job_id", ""),
            "status": result.get("status", "running"),
            "started_at": result.get("started_at", ""),
        }

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "exec", payload,
        detail=command, key_info=key_info, timeout=timeout + 5,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    await _log_operation(
        binding, "exec", command, key_info,
        duration_ms=duration_ms,
        exit_code=result.get("exit_code"),
        stdout_len=len(result.get("stdout", "")),
        stderr_len=len(result.get("stderr", "")),
    )

    if format == "text":
        rendered = _format_exec_text(result)
        return _maybe_truncate_text(
            rendered,
            max_bytes=max_bytes,
            continue_hint=(
                "redirect/filter stdout in the command (e.g. `| tail -N`) "
                "or run a narrower query"
            ),
        )

    return {
        "exit_code": result.get("exit_code", -1),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "stdout_truncated": result.get("stdout_truncated", False),
        "stderr_truncated": result.get("stderr_truncated", False),
        "stdout_total_bytes": result.get("stdout_total_bytes", 0),
        "stderr_total_bytes": result.get("stderr_total_bytes", 0),
        "duration_ms": duration_ms,
    }


def _format_exec_text(result: dict) -> str:
    """Render an exec result as Bash-like plain text.

    Layout (sections omitted when empty/zero):
        <stdout>
        [stderr]
        <stderr>
        [exit N]
        [stdout truncated at M bytes]
        [stderr truncated at M bytes]
    """
    stdout = result.get("stdout", "") or ""
    stderr = result.get("stderr", "") or ""
    exit_code = result.get("exit_code", -1)

    parts: list[str] = []
    if stdout:
        parts.append(stdout)
        if not stdout.endswith("\n"):
            parts.append("\n")

    if stderr:
        parts.append("[stderr]\n")
        parts.append(stderr)
        if not stderr.endswith("\n"):
            parts.append("\n")

    if exit_code != 0:
        parts.append(f"[exit {exit_code}]\n")

    if result.get("stdout_truncated"):
        total = result.get("stdout_total_bytes", 0)
        parts.append(f"[stdout truncated at {total} bytes]\n")
    if result.get("stderr_truncated"):
        total = result.get("stderr_total_bytes", 0)
        parts.append(f"[stderr truncated at {total} bytes]\n")

    return "".join(parts)


@mcp.tool()
async def remote_exec_status(
    project_id: str,
    job_id: str,
) -> dict:
    """Poll status of a background command (``remote_exec run_in_background=True``).

    Args:
        project_id: Project ID or name
        job_id: Job ID from ``remote_exec``

    Returns:
        dict with ``job_id``, ``status`` (``running``/``completed``),
        ``command``, ``started_at``. When completed also includes
        ``exit_code``, ``stdout``, ``stderr``, ``completed_at``,
        ``duration_ms``, truncation flags.
    """
    async with _audit_on_denied("exec_status", project_id, detail=job_id) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "exec_status", {"job_id": job_id},
        detail=job_id, key_info=key_info, timeout=10,
    )
    return result


@mcp.tool()
async def remote_read_file(
    project_id: str,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
    encoding: str = "utf-8",
    format: str = "text",
    if_not_hash: str | None = None,
    max_bytes: int | None = None,
) -> dict | str:
    """Read a file on the remote machine for this project.

    Path is relative to the project's remote directory or absolute
    (must resolve inside workspace).

    Args:
        project_id: Project ID or name
        path: File path
        offset: Starting line (1-based; text mode only)
        limit: Line count (text mode only)
        encoding: ``"utf-8"`` (default) / ``"utf-16"`` / ``"shift_jis"`` /
            etc. Use ``"binary"``/``"base64"`` for binary files.
        format: ``"text"`` (default, ``cat -n``-style ``N<TAB><line>\\n``)
            or ``"json"``. Binary content always returns a dict.
        if_not_hash: Conditional read. Pass a previously-returned content
            sha256 hex to skip the full payload when unchanged. Matches
            → compact ``unchanged sha256:<hash>`` / ``{"unchanged": true,
            "hash": ...}``. Mismatch → full content + new hash (so the
            caller can refresh its cache).

    Returns:
        ``format="text"`` → ``str`` with ``<line>\\t<content>`` per line.
        Appends ``[truncated at N total lines]`` when truncated. When
        ``if_not_hash`` is supplied, a final ``[sha256:<hash>]`` line is
        added so the caller can cache. On hash match, returns only
        ``unchanged sha256:<hash>\\n``.

        ``format="json"`` / binary → dict with ``content``, ``size``,
        ``path``, ``encoding``, ``is_binary``, ``total_lines``,
        ``truncated``. Includes ``hash`` when ``if_not_hash`` was given.
        On hash match, returns ``{"unchanged": true, "hash": "..."}``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    if if_not_hash is not None and not isinstance(if_not_hash, str):
        raise ToolError("if_not_hash must be a string (sha256 hex) or null")
    async with _audit_on_denied("read_file", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
        if offset is not None and offset < 0:
            raise ToolError("offset must be >= 0")
        # Callers may send 0-based offset; normalise to 1-based for the agent
        if offset is not None and offset < 1:
            offset = 1
        if limit is not None and limit < 0:
            raise ToolError("limit must be >= 0")
    key_info = audit.key_info
    binding = audit.binding

    payload: dict = {"path": path, "cwd": binding.remote_path}
    if offset is not None:
        payload["offset"] = offset
    if limit is not None:
        payload["limit"] = limit
    if encoding:
        payload["encoding"] = encoding

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "read_file", payload,
        detail=path, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    content = result.get("content", "")
    is_binary = result.get("is_binary", False)

    await _log_operation(
        binding, "read_file", path, key_info,
        duration_ms=duration_ms, stdout_len=len(content),
    )

    # Content hash is computed lazily — only when the caller opted into
    # conditional reads. Skip the hash work otherwise to keep the fast
    # path cheap.
    content_hash: str | None = None
    if if_not_hash is not None:
        import hashlib
        content_hash = hashlib.sha256(
            content.encode("utf-8") if isinstance(content, str) else content
        ).hexdigest()
        if content_hash == if_not_hash:
            # Cache hit — return compact "unchanged" marker without
            # re-sending the content.
            if format == "text":
                return f"unchanged sha256:{content_hash}\n"
            return {"unchanged": True, "hash": content_hash}

    # Text mode: emit Read-compatible `N\t<line>\n` output. Binary content
    # cannot be rendered as text, so fall through to the dict branch.
    if (
        format == "text"
        and not is_binary
        and encoding not in ("binary", "base64")
    ):
        rendered = _format_read_text(
            content,
            start_line=offset if offset else 1,
            truncated=result.get("truncated", False),
            total_lines=result.get("total_lines", 0),
        )
        rendered = _maybe_truncate_text(
            rendered,
            max_bytes=max_bytes,
            continue_hint=(
                "pass offset= and limit= to read a specific line range"
            ),
        )
        if content_hash is not None:
            rendered = f"{rendered}[sha256:{content_hash}]\n"
        return rendered

    dict_result = {
        "content": content,
        "size": result.get("size", len(content)),
        "path": result.get("path", path),
        "encoding": result.get("encoding", encoding),
        "is_binary": is_binary,
        "total_lines": result.get("total_lines", 0),
        "truncated": result.get("truncated", False),
    }
    if content_hash is not None:
        dict_result["hash"] = content_hash
    return dict_result


def _format_read_text(
    content: str,
    *,
    start_line: int,
    truncated: bool,
    total_lines: int,
) -> str:
    """Render file content as ``cat -n``-style ``N<TAB><line>\\n`` text.

    Matches the local ``Read`` tool format so callers can use remote files
    with the same mental model as local reads.
    """
    if not content:
        return ""
    # ``content.split("\n")`` preserves the trailing empty element when the
    # content ends with a newline — this mirrors cat -n which renders an
    # extra numbered line for the final LF.
    lines = content.split("\n")
    out = [f"{start_line + i}\t{line}\n" for i, line in enumerate(lines)]
    if truncated:
        out.append(f"[truncated at {total_lines} total lines]\n")
    return "".join(out)


@mcp.tool()
async def remote_write_file(
    project_id: str,
    path: str,
    content: str,
    format: str = "text",
) -> dict | str:
    """Write a file on the remote machine (creates parent dirs).

    Args:
        project_id: Project ID or name
        path: Destination file path
        content: File contents
        format: ``"text"`` (default, ``wrote N bytes to <path>``) or ``"json"``

    Returns:
        ``format="text"`` → ``str`` single-line confirmation.

        ``format="json"`` → dict with ``success``, ``bytes_written``, ``path``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    async with _audit_on_denied("write_file", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
        if len(content.encode("utf-8")) > settings.REMOTE_MAX_FILE_BYTES:
            raise ToolError(
                f"Content too large (max {settings.REMOTE_MAX_FILE_BYTES // 1024 // 1024} MB)"
            )
    key_info = audit.key_info
    binding = audit.binding

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "write_file",
        {"path": path, "cwd": binding.remote_path, "content": content},
        detail=path, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    bytes_written = result.get("bytes_written", 0)
    result_path = result.get("path", path)
    await _log_operation(
        binding, "write_file", path, key_info,
        duration_ms=duration_ms, stdout_len=bytes_written,
    )

    if format == "text":
        return f"wrote {bytes_written} bytes to {result_path}\n"

    return {
        "success": result.get("success", True),
        "bytes_written": bytes_written,
        "path": result_path,
    }


@mcp.tool()
async def remote_edit_file(
    project_id: str,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    format: str = "text",
) -> dict | str:
    """String-replace edit on a remote file (diff-only wire transfer).

    Args:
        project_id: Project ID or name
        path: File path
        old_string: Exact text to find (must exist)
        new_string: Replacement text (must differ)
        replace_all: Replace every occurrence. Default ``False`` — requires
            unique match, errors on ambiguity.
        format: ``"text"`` (default, ``edited <path>`` or
            ``edited <path> (N replacements)``) or ``"json"``

    Returns:
        ``format="text"`` → ``str`` single-line confirmation.

        ``format="json"`` → dict with ``success``, ``path``, ``replacements``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    async with _audit_on_denied("edit_file", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
        if not old_string:
            raise ToolError("old_string is required")
        if old_string == new_string:
            raise ToolError("old_string and new_string must differ")
    key_info = audit.key_info
    binding = audit.binding

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "edit_file",
        {
            "path": path,
            "cwd": binding.remote_path,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        },
        detail=path, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    if not result.get("success", False):
        await _log_operation(
            binding, "edit_file", path, key_info,
            duration_ms=duration_ms, error=result.get("error", "unknown"),
        )
        raise ToolError(result.get("error", "edit_file failed"))

    await _log_operation(
        binding, "edit_file", path, key_info,
        duration_ms=duration_ms,
    )

    result_path = result.get("path", path)
    replacements = result.get("replacements", 1)

    if format == "text":
        if replacements == 1:
            return f"edited {result_path}\n"
        return f"edited {result_path} ({replacements} replacements)\n"

    return {
        "success": True,
        "path": result_path,
        "replacements": replacements,
    }


@mcp.tool()
async def remote_list_dir(
    project_id: str,
    path: str = ".",
    format: str = "text",
) -> dict | str:
    """List directory contents on the remote machine.

    Args:
        project_id: Project ID or name
        path: Directory path (default ``.``)
        format: ``"text"`` (default, ``ls -p``-style one entry per line,
            dirs suffixed with ``/``) or ``"json"``

    Returns:
        ``format="text"`` → ``str``.

        ``format="json"`` → dict with ``entries`` (``[{name, type, size,
        mtime}]``), ``count``, ``path``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    async with _audit_on_denied("list_dir", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
    key_info = audit.key_info
    binding = audit.binding

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "list_dir",
        {"path": path, "cwd": binding.remote_path},
        detail=path, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    entries = result.get("entries", [])
    await _log_operation(
        binding, "list_dir", path, key_info,
        duration_ms=duration_ms, stdout_len=len(entries),
    )

    if format == "text":
        return _format_list_dir_text(entries)

    return {
        "entries": entries,
        "count": len(entries),
        "path": result.get("path", path),
    }


def _format_list_dir_text(entries: list) -> str:
    """Render directory entries as plain text, one per line.

    Directories are suffixed with ``/`` (``ls -p`` style). Preserves the
    agent's entry order so callers can rely on consistent sort.
    """
    lines: list[str] = []
    for e in entries:
        name = e.get("name", "")
        if not name:
            continue
        # Agent reports directories as ``type == "dir"``; accept the
        # spelled-out form too for resilience.
        is_dir = e.get("type") in ("dir", "directory")
        suffix = "/" if is_dir else ""
        lines.append(f"{name}{suffix}\n")
    return "".join(lines)


# ── New tools (stat / file_exists / mkdir / delete / move / copy / glob / grep) ──


@mcp.tool()
async def remote_stat(project_id: str, path: str) -> dict:
    """Return metadata for a remote path: type / size / mtime / mode.

    Returns ``{exists: false, type: null}`` for missing paths instead of
    raising — callers can use this as a cheap existence check before a
    full ``remote_read_file``.
    """
    async with _audit_on_denied("stat", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "stat",
        {"path": path, "cwd": binding.remote_path},
        detail=path, key_info=key_info,
    )
    await _log_operation(binding, "stat", path, key_info)
    return result


@mcp.tool()
async def remote_file_exists(project_id: str, path: str) -> dict:
    """Cheap existence check. Returns ``{exists, type}`` only.

    Equivalent to ``remote_stat`` but returns the minimal subset; useful
    when you only need a yes/no answer (e.g. before creating a file).
    """
    async with _audit_on_denied("stat", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "stat",
        {"path": path, "cwd": binding.remote_path},
        detail=path, key_info=key_info,
    )
    return {
        "exists": result.get("exists", False),
        "type": result.get("type"),
    }


@mcp.tool()
async def remote_mkdir(project_id: str, path: str, parents: bool = True) -> dict:
    """Create a directory on the remote machine.

    With ``parents=True`` (default), missing parents are created (mkdir -p).
    """
    async with _audit_on_denied("mkdir", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "mkdir",
        {"path": path, "cwd": binding.remote_path, "parents": parents},
        detail=path, key_info=key_info,
    )
    await _log_operation(binding, "mkdir", path, key_info)
    return result


@mcp.tool()
async def remote_delete_file(
    project_id: str, path: str, recursive: bool = False
) -> dict:
    """Delete a file or directory on the remote machine.

    Directories require ``recursive=True``. Refuses to delete the
    workspace root.
    """
    async with _audit_on_denied("delete", project_id, detail=str(path)[:500]) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "delete",
        {"path": path, "cwd": binding.remote_path, "recursive": recursive},
        detail=path, key_info=key_info,
    )
    await _log_operation(binding, "delete", path, key_info)
    return result


@mcp.tool()
async def remote_move_file(
    project_id: str, src: str, dst: str, overwrite: bool = False
) -> dict:
    """Move/rename a file or directory on the remote machine."""
    async with _audit_on_denied(
        "move", project_id, detail=f"{src} -> {dst}"[:500],
    ) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(src)
        _validate_remote_path(dst)
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "move",
        {"src": src, "dst": dst, "cwd": binding.remote_path, "overwrite": overwrite},
        detail=f"{src} -> {dst}", key_info=key_info,
    )
    await _log_operation(binding, "move", f"{src} -> {dst}", key_info)
    return result


@mcp.tool()
async def remote_copy_file(
    project_id: str, src: str, dst: str, overwrite: bool = False
) -> dict:
    """Copy a file or directory on the remote machine."""
    async with _audit_on_denied(
        "copy", project_id, detail=f"{src} -> {dst}"[:500],
    ) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(src)
        _validate_remote_path(dst)
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "copy",
        {"src": src, "dst": dst, "cwd": binding.remote_path, "overwrite": overwrite},
        detail=f"{src} -> {dst}", key_info=key_info,
    )
    await _log_operation(binding, "copy", f"{src} -> {dst}", key_info)
    return result


@mcp.tool()
async def remote_glob(
    project_id: str, pattern: str, path: str = ".",
    format: str = "text",
) -> dict | str:
    """Find files by glob pattern (mtime-desc sorted, like local Glob).

    Pattern semantics: Python ``pathlib.Path.glob`` (``**`` = recursive).

    Args:
        project_id: Project ID or name
        pattern: Glob, e.g. ``**/*.py``
        path: Base directory (default ``.``)
        format: ``"text"`` (default, one path per line) or ``"json"``

    Returns:
        ``format="text"`` → ``str`` (1 path/line, mtime desc), appends
        ``[truncated]`` when truncated.

        ``format="json"`` → dict with ``matches``, ``count``, ``base``,
        ``truncated``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    async with _audit_on_denied(
        "glob", project_id, detail=f"{pattern} @ {path}"[:500],
    ) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
        _validate_remote_pattern(pattern, kind="glob")
    key_info = audit.key_info
    binding = audit.binding

    result = await _send_to_agent(
        binding, "glob",
        {"pattern": pattern, "path": path, "cwd": binding.remote_path},
        detail=f"{pattern} @ {path}", key_info=key_info,
    )
    await _log_operation(binding, "glob", f"{pattern} @ {path}", key_info)

    if format == "text":
        return _format_glob_text(result)
    return result


def _format_glob_text(result: dict) -> str:
    """Render glob matches as plain paths, one per line."""
    matches = result.get("matches", []) or []
    lines = [f"{m.get('path', '')}\n" for m in matches if m.get("path")]
    if result.get("truncated"):
        lines.append("[truncated]\n")
    return "".join(lines)


@mcp.tool()
async def remote_grep(
    project_id: str,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    case_insensitive: bool = False,
    max_results: int = 200,
    respect_gitignore: bool = False,
    context_lines: int = 0,
    output_mode: str = "content",
    format: str = "text",
    max_bytes: int | None = None,
) -> dict | str:
    """Regex search in files under ``path`` (ripgrep / Python fallback).

    Automatically skips vendored dirs (.git, node_modules, .venv, etc),
    binary files, and files >10 MB. See docs/mcp-tools/remote.md for the
    full list.

    Args:
        project_id: Project ID or name
        pattern: Regex to search for
        path: Base directory (default ``.``)
        glob: File-name glob filter (e.g. ``*.py``)
        case_insensitive: Case-insensitive match (ripgrep ``-i``)
        max_results: Max matches (1-2000, default 200)
        respect_gitignore: Honor ``.gitignore`` (ripgrep only; Python
            fallback never reads gitignore)
        context_lines: Lines before/after each match (0-20, ripgrep ``-C``)
        output_mode: ``"content"`` (default, ``path:line:text``),
            ``"files_with_matches"`` (unique paths), or ``"count"``
            (``path:N``). Ignored when ``format="json"``.
        format: ``"text"`` (default) or ``"json"``

    Returns:
        ``format="text"`` → ``str`` in ripgrep format, with
        ``[truncated at N matches]`` suffix when applicable.

        ``format="json"`` → dict with ``matches``, ``count``, ``truncated``,
        ``files_scanned``, ``engine``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    if output_mode not in ("content", "files_with_matches", "count"):
        raise ToolError(
            "output_mode must be 'content', 'files_with_matches', or 'count'"
        )
    async with _audit_on_denied(
        "grep", project_id, detail=f"{pattern} @ {path}"[:500],
    ) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        _validate_remote_path(path)
        _validate_remote_pattern(pattern, kind="grep")
        if not isinstance(max_results, int) or max_results < 1 or max_results > 2000:
            raise ToolError("max_results must be an integer between 1 and 2000")
        if not isinstance(context_lines, int) or context_lines < 0 or context_lines > 20:
            raise ToolError("context_lines must be an integer between 0 and 20")
    key_info = audit.key_info
    binding = audit.binding

    payload: dict = {
        "pattern": pattern,
        "path": path,
        "cwd": binding.remote_path,
        "case_insensitive": case_insensitive,
        "max_results": max_results,
        "respect_gitignore": respect_gitignore,
        "context_lines": context_lines,
    }
    if glob:
        payload["glob"] = glob

    result = await _send_to_agent(
        binding, "grep", payload,
        detail=f"{pattern} @ {path}", key_info=key_info,
    )
    await _log_operation(binding, "grep", f"{pattern} @ {path}", key_info)

    if format == "text":
        rendered = _format_grep_text(
            result, output_mode=output_mode, max_results=max_results,
        )
        return _maybe_truncate_text(
            rendered,
            max_bytes=max_bytes,
            continue_hint=(
                "narrow the pattern, pass glob=, or lower max_results"
            ),
        )
    return result


def _format_grep_text(
    result: dict, *, output_mode: str, max_results: int,
) -> str:
    """Render grep results as ripgrep-style text.

    Matches local ``Grep`` tool output so callers can use remote grep with
    the same mental model. Normalises line terminators — upstream ``text``
    may carry a trailing ``\\r`` or ``\\n`` from the source file.
    """
    matches = result.get("matches", []) or []
    truncated = result.get("truncated", False)

    if output_mode == "files_with_matches":
        seen: set[str] = set()
        lines: list[str] = []
        for m in matches:
            f = m.get("file", "")
            if f and f not in seen:
                seen.add(f)
                lines.append(f"{f}\n")
        if truncated:
            lines.append("[truncated: more files may exist]\n")
        return "".join(lines)

    if output_mode == "count":
        counts: dict[str, int] = {}
        for m in matches:
            f = m.get("file", "")
            if f:
                counts[f] = counts.get(f, 0) + 1
        out_lines = [f"{f}:{n}\n" for f, n in counts.items()]
        if truncated:
            out_lines.append("[truncated: counts are lower bound]\n")
        return "".join(out_lines)

    # output_mode == "content" — ripgrep "path:line:text" with '-' for context
    out: list[str] = []
    for m in matches:
        f = m.get("file", "")
        line = m.get("line", 0)
        for c in m.get("context_before", []) or []:
            out.append(
                f"{f}-{c.get('line', 0)}-{_strip_line(c.get('text', ''))}\n"
            )
        out.append(f"{f}:{line}:{_strip_line(m.get('text', ''))}\n")
        for c in m.get("context_after", []) or []:
            out.append(
                f"{f}-{c.get('line', 0)}-{_strip_line(c.get('text', ''))}\n"
            )
    if truncated:
        out.append(f"[truncated at {max_results} matches]\n")
    return "".join(out)


def _strip_line(s: str) -> str:
    """Strip a single trailing newline/CR pair from a grep text line."""
    if s.endswith("\r\n"):
        return s[:-2]
    if s.endswith("\n") or s.endswith("\r"):
        return s[:-1]
    return s


def _maybe_truncate_text(
    text: str,
    *,
    max_bytes: int | None,
    continue_hint: str = "",
) -> str:
    """Auto-truncate oversized text with a head/tail + omitted-bytes hint.

    Returns the input unchanged when ``max_bytes`` is ``None`` or the
    text fits. Otherwise returns ``head + marker + tail`` where head and
    tail each take ~half the budget. The marker includes the omitted
    byte count and an optional ``continue_hint`` telling the caller how
    to retrieve the missing range (e.g. ``offset=N, limit=M``).

    Head/tail are snapped to line boundaries so rendered text stays
    readable.
    """
    if max_bytes is None or len(text) <= max_bytes:
        return text

    omitted = len(text) - max_bytes
    hint_part = f". {continue_hint}" if continue_hint else ""
    marker = f"\n[... {omitted} bytes omitted{hint_part} ...]\n"

    # Budget head/tail around the marker.
    available = max(0, max_bytes - len(marker))
    head_size = available // 2
    tail_size = available - head_size

    head = text[:head_size]
    if head_size > 0:
        nl = head.rfind("\n")
        if nl > head_size // 2:
            head = head[: nl + 1]

    tail = text[-tail_size:] if tail_size > 0 else ""
    if tail_size > 0:
        nl = tail.find("\n")
        if 0 <= nl < tail_size // 2:
            tail = tail[nl + 1 :]

    return head + marker + tail


# ──────────────────────────────────────────────
# Batch endpoints — amortize MCP framing overhead when the caller has
# multiple reads/commands to issue in a row.
# ──────────────────────────────────────────────

MAX_BATCH_FILES = 20
MAX_BATCH_COMMANDS = 10
MAX_BATCH_BYTES = 1_000_000  # 1 MB soft cap on aggregate read output


@mcp.tool()
async def remote_read_files(
    project_id: str,
    paths: list[str],
    encoding: str = "utf-8",
    format: str = "text",
) -> dict | str:
    """Read multiple files in a single MCP call (amortizes framing cost).

    Each path is read sequentially via the same agent protocol as
    ``remote_read_file``. Per-file errors are surfaced inline so one bad
    path doesn't poison the whole batch.

    Args:
        project_id: Project ID or name
        paths: File paths (max ``MAX_BATCH_FILES`` = 20)
        encoding: Applied to every file
        format: ``"text"`` (default) → header-separated concatenation:
            ``=== <path> ===\\n<content>\\n``. Per-file errors render as
            ``=== <path> ===\\n[error: <msg>]\\n``.
            ``"json"`` → dict with ``files`` list.

    Returns:
        ``format="text"`` → ``str`` with ``=== <path> ===`` headers.

        ``format="json"`` → ``{"files": [{path, content?, error?, size,
        total_lines, truncated}], "count": N, "errors": M}``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    if not isinstance(paths, list) or not paths:
        raise ToolError("paths must be a non-empty list of strings")
    if len(paths) > MAX_BATCH_FILES:
        raise ToolError(f"paths exceeds the {MAX_BATCH_FILES}-file batch limit")

    async with _audit_on_denied(
        "read_files", project_id, detail=f"{len(paths)} paths",
    ) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        for p in paths:
            if not isinstance(p, str):
                raise ToolError("all paths must be strings")
            _validate_remote_path(p)
    key_info = audit.key_info
    binding = audit.binding

    files_out: list[dict] = []
    total_bytes = 0
    errors = 0
    for path in paths:
        try:
            res = await _send_to_agent(
                binding, "read_file",
                {"path": path, "cwd": binding.remote_path, "encoding": encoding},
                detail=path, key_info=key_info,
            )
            content = res.get("content", "") or ""
            total_bytes += len(content)
            files_out.append({
                "path": res.get("path", path),
                "content": content,
                "size": res.get("size", len(content)),
                "encoding": res.get("encoding", encoding),
                "is_binary": res.get("is_binary", False),
                "total_lines": res.get("total_lines", 0),
                "truncated": res.get("truncated", False),
            })
            if total_bytes > MAX_BATCH_BYTES:
                # Cap aggregate size to keep the response bounded. The
                # caller can retry the remaining paths in a follow-up call.
                files_out.append({
                    "path": "",
                    "error": (
                        f"batch aggregate exceeded {MAX_BATCH_BYTES} bytes "
                        f"after {len(files_out)}/{len(paths)} files; retry "
                        f"remaining in a new call"
                    ),
                })
                errors += 1
                break
        except ToolError as e:
            errors += 1
            files_out.append({"path": path, "error": str(e)})

    await _log_operation(
        binding, "read_files", f"{len(paths)} paths", key_info,
        stdout_len=total_bytes,
    )

    if format == "text":
        parts: list[str] = []
        for f in files_out:
            p = f.get("path", "")
            parts.append(f"=== {p} ===\n")
            if "error" in f:
                parts.append(f"[error: {f['error']}]\n")
                continue
            content = f.get("content", "")
            if content and not content.endswith("\n"):
                parts.append(content)
                parts.append("\n")
            else:
                parts.append(content)
        return "".join(parts)

    return {
        "files": files_out,
        "count": len(files_out),
        "errors": errors,
    }


@mcp.tool()
async def remote_exec_batch(
    project_id: str,
    commands: list[str],
    timeout: int = 60,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    inject_secrets: bool = False,
    stop_on_error: bool = False,
    format: str = "text",
) -> dict | str:
    """Run multiple shell commands in one MCP call (amortizes framing cost).

    Commands run serially on the agent. Sharing a single call lets the
    caller avoid per-command MCP/schema overhead.

    Args:
        project_id: Project ID or name
        commands: Shell commands (max ``MAX_BATCH_COMMANDS`` = 10)
        timeout: Per-command timeout (1-3600, default 60)
        cwd: Optional subdirectory for all commands
        env: Extra env vars for all commands
        inject_secrets: Inject project secrets into env (same semantics as
            ``remote_exec``)
        stop_on_error: If ``True``, halt after the first non-zero exit.
            Default ``False`` runs all commands and collects results.
        format: ``"text"`` (default) → per-command blocks with
            ``$ <command>\\n<stdout>\\n[exit N]\\n``. ``"json"`` → list dict.

    Returns:
        ``format="text"`` → ``str`` with per-command blocks.

        ``format="json"`` → ``{"commands": [{command, exit_code, stdout,
        stderr, duration_ms}], "count": N, "stopped": bool}``.
    """
    if format not in ("text", "json"):
        raise ToolError("format must be 'text' or 'json'")
    if not isinstance(commands, list) or not commands:
        raise ToolError("commands must be a non-empty list of strings")
    if len(commands) > MAX_BATCH_COMMANDS:
        raise ToolError(
            f"commands exceeds the {MAX_BATCH_COMMANDS}-command batch limit"
        )

    async with _audit_on_denied(
        "exec_batch", project_id, detail=f"{len(commands)} commands",
    ) as audit:
        audit.key_info = await authenticate()
        audit.binding = await _resolve_binding(project_id, audit.key_info)
        for c in commands:
            if not isinstance(c, str):
                raise ToolError("all commands must be strings")
            _validate_remote_command(c)
        if cwd is not None:
            _validate_remote_path(cwd)
        if env is not None:
            _validate_remote_env(env)
    key_info = audit.key_info
    binding = audit.binding

    # inject_secrets — same path as single remote_exec
    if inject_secrets:
        from ...models.secret import ProjectSecret, SecretAccessLog

        secrets = await ProjectSecret.find(
            ProjectSecret.project_id == binding.project_id,
        ).to_list()
        if secrets:
            secret_env = {s.key: s.value for s in secrets}
            if env is not None:
                secret_env.update(env)
            env = secret_env
            _validate_remote_env(env)
            for s in secrets:
                await SecretAccessLog(
                    project_id=binding.project_id,
                    secret_key=s.key,
                    operation="inject",
                    user_id=key_info.get("user_id", ""),
                    auth_kind=key_info.get("auth_kind", ""),
                ).insert()

    timeout = max(1, min(timeout, settings.REMOTE_MAX_TIMEOUT_SECONDS))
    results: list[dict] = []
    stopped = False
    for command in commands:
        payload: dict = {
            "command": command,
            "cwd": binding.remote_path,
            "timeout": timeout,
        }
        if cwd is not None:
            payload["cwd_override"] = cwd
        if env is not None:
            payload["env"] = env

        t0 = time.monotonic()
        try:
            res = await _send_to_agent(
                binding, "exec", payload,
                detail=command, key_info=key_info, timeout=timeout + 5,
            )
        except ToolError as e:
            results.append({
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "duration_ms": int((time.monotonic() - t0) * 1000),
            })
            if stop_on_error:
                stopped = True
                break
            continue
        duration_ms = int((time.monotonic() - t0) * 1000)
        exit_code = res.get("exit_code", -1)
        results.append({
            "command": command,
            "exit_code": exit_code,
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
            "duration_ms": duration_ms,
        })
        await _log_operation(
            binding, "exec", command, key_info,
            duration_ms=duration_ms,
            exit_code=exit_code,
            stdout_len=len(res.get("stdout", "")),
            stderr_len=len(res.get("stderr", "")),
        )
        if stop_on_error and exit_code != 0:
            stopped = True
            break

    if format == "text":
        parts: list[str] = []
        for r in results:
            parts.append(f"$ {r['command']}\n")
            stdout = r.get("stdout", "")
            if stdout:
                parts.append(stdout)
                if not stdout.endswith("\n"):
                    parts.append("\n")
            stderr = r.get("stderr", "")
            if stderr:
                parts.append("[stderr]\n")
                parts.append(stderr)
                if not stderr.endswith("\n"):
                    parts.append("\n")
            if r.get("exit_code", 0) != 0:
                parts.append(f"[exit {r['exit_code']}]\n")
        if stopped:
            parts.append(
                f"[stopped after {len(results)}/{len(commands)} commands "
                f"due to non-zero exit]\n"
            )
        return "".join(parts)

    return {
        "commands": results,
        "count": len(results),
        "stopped": stopped,
    }
