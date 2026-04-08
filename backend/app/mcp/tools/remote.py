"""MCP tools for remote command execution and file operations via connected agents."""

import logging
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastmcp.exceptions import ToolError

from ...models import Project
from ...models.remote import RemoteAgent, RemoteExecLog


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

MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_TIMEOUT = 300  # seconds
MAX_COMMAND_BYTES = 8 * 1024  # 8 KB — upper bound for a single shell command
MAX_PATTERN_BYTES = 4 * 1024  # 4 KB — upper bound for grep/glob patterns
DEFAULT_AGENT_WAIT = 5.0  # seconds to wait for a flickering agent


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
    mcp_key_id: str,
    duration_ms: int = 0,
    exit_code: int | None = None,
    stdout_len: int = 0,
    stderr_len: int = 0,
    error: str = "",
) -> None:
    """Record operation to audit log."""
    try:
        log = RemoteExecLog(
            project_id=binding.project_id,
            agent_id=binding.agent_id,
            operation=operation,
            detail=detail[:500],
            exit_code=exit_code,
            stdout_len=stdout_len,
            stderr_len=stderr_len,
            duration_ms=duration_ms,
            error=error[:500],
            mcp_key_id=mcp_key_id,
        )
        await log.insert()
    except Exception as e:
        logger.warning("Failed to log remote operation: %s", e)


async def _send_to_agent(
    binding: ResolvedBinding,
    msg_type: str,
    payload: dict,
    timeout: float,
    operation: str,
    detail: str,
    key_info: dict,
) -> dict:
    """Common request/response wrapper with audit logging + agent wait.

    Centralizes the AgentOfflineError / CommandTimeoutError / RuntimeError
    handling so individual MCP tools stay short.
    """
    t0 = time.monotonic()
    try:
        result = await agent_manager.send_request(
            binding.agent_id,
            msg_type,
            payload,
            timeout=timeout,
            wait_for_agent=DEFAULT_AGENT_WAIT,
        )
    except AgentOfflineError:
        await _log_operation(binding, operation, detail, key_info["key_id"], error="agent_offline")
        raise ToolError("Agent is offline")
    except CommandTimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _log_operation(binding, operation, detail, key_info["key_id"],
                             duration_ms=duration_ms, error="timeout")
        raise ToolError(f"Request timed out after {timeout}s")
    except RuntimeError as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _log_operation(binding, operation, detail, key_info["key_id"],
                             duration_ms=duration_ms, error=str(e))
        raise ToolError(str(e))
    return result


@mcp.tool()
async def list_remote_agents() -> list[dict]:
    """List registered remote agents and their connection status.

    Returns a list of agents with id, name, hostname, os_type, is_online,
    and the number of projects bound to the agent.
    """
    await authenticate()

    agents = await RemoteAgent.find_all().to_list()
    result = []
    for a in agents:
        aid = str(a.id)
        project_count = await Project.find({"remote.agent_id": aid}).count()
        result.append({
            "id": aid,
            "name": a.name,
            "hostname": a.hostname,
            "os_type": a.os_type,
            "is_online": agent_manager.is_connected(aid),
            "project_count": project_count,
            "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
            "agent_version": a.agent_version,
            "auto_update": a.auto_update,
            "update_channel": a.update_channel,
        })
    return result


@mcp.tool()
async def remote_exec(
    project_id: str,
    command: str,
    timeout: int = 60,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    """Execute a shell command on the remote machine linked to this project.

    The command runs in the project's configured remote directory (cwd).
    Supports any shell command including git, docker, npm, etc.

    Args:
        project_id: Project ID or project name
        command: Shell command to execute
        timeout: Execution timeout in seconds (1-300, default 60)
        cwd: Optional subdirectory inside the workspace to run in. May be
            relative to the workspace root or an absolute path inside it.
            Path traversal outside the workspace is rejected.
        env: Optional dict of extra environment variables. Merged with the
            agent's existing environment (so PATH and friends survive).
            Cross-platform alternative to ``set X=Y && cmd`` chains.

    Returns:
        dict with ``exit_code``, ``stdout``, ``stderr``, ``duration_ms``,
        plus ``stdout_truncated`` / ``stderr_truncated`` flags and the
        original ``stdout_total_bytes`` / ``stderr_total_bytes`` so callers
        can detect output that exceeded the 2MB agent buffer.
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_command(command)

    timeout = max(1, min(timeout, MAX_TIMEOUT))
    payload: dict = {
        "command": command,
        "cwd": binding.remote_path,
        "timeout": timeout,
    }
    if cwd is not None:
        _validate_remote_path(cwd)
        payload["cwd_override"] = cwd
    if env is not None:
        if not isinstance(env, dict):
            raise ToolError("env must be a dict of string→string")
        for k, v in env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ToolError("env keys and values must be strings")
        payload["env"] = env

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "exec", payload, timeout=timeout + 5,
        operation="exec", detail=command, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)

    await _log_operation(
        binding, "exec", command, key_info["key_id"],
        duration_ms=duration_ms,
        exit_code=result.get("exit_code"),
        stdout_len=len(result.get("stdout", "")),
        stderr_len=len(result.get("stderr", "")),
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


@mcp.tool()
async def remote_read_file(
    project_id: str,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
    encoding: str = "utf-8",
) -> dict:
    """Read a file on the remote machine linked to this project.

    Path is relative to the project's remote directory, or absolute (must
    still resolve inside the workspace).

    Args:
        project_id: Project ID or project name
        path: File path
        offset: 1-based starting line number (text mode only)
        limit: Number of lines to read (text mode only)
        encoding: 'utf-8' (default), 'utf-16', 'shift_jis', 'latin-1', etc.
            Use 'binary' or 'base64' for binary files — content is then
            base64-encoded with ``is_binary=True``.

    Returns:
        dict with ``content``, ``size``, ``path``, ``encoding``,
        ``is_binary``, ``total_lines``, ``truncated``.
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)

    payload: dict = {"path": path, "cwd": binding.remote_path}
    if offset is not None:
        if offset < 1:
            raise ToolError("offset must be >= 1")
        payload["offset"] = offset
    if limit is not None:
        if limit < 0:
            raise ToolError("limit must be >= 0")
        payload["limit"] = limit
    if encoding:
        payload["encoding"] = encoding

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "read_file", payload, timeout=30,
        operation="read_file", detail=path, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    content = result.get("content", "")

    await _log_operation(
        binding, "read_file", path, key_info["key_id"],
        duration_ms=duration_ms, stdout_len=len(content),
    )

    return {
        "content": content,
        "size": result.get("size", len(content)),
        "path": result.get("path", path),
        "encoding": result.get("encoding", encoding),
        "is_binary": result.get("is_binary", False),
        "total_lines": result.get("total_lines", 0),
        "truncated": result.get("truncated", False),
    }


@mcp.tool()
async def remote_write_file(
    project_id: str,
    path: str,
    content: str,
) -> dict:
    """Write a file on the remote machine linked to this project.

    Path is relative to the project's remote directory, or absolute.
    Parent directories are created automatically.
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)

    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ToolError(f"Content too large (max {MAX_FILE_BYTES // 1024 // 1024} MB)")

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "write_file",
        {"path": path, "cwd": binding.remote_path, "content": content},
        timeout=30, operation="write_file", detail=path, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    await _log_operation(
        binding, "write_file", path, key_info["key_id"],
        duration_ms=duration_ms, stdout_len=result.get("bytes_written", 0),
    )

    return {
        "success": result.get("success", True),
        "bytes_written": result.get("bytes_written", 0),
        "path": result.get("path", path),
    }


@mcp.tool()
async def remote_list_dir(
    project_id: str,
    path: str = ".",
) -> dict:
    """List directory contents on the remote machine linked to this project.

    Path is relative to the project's remote directory, or absolute.
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)

    t0 = time.monotonic()
    result = await _send_to_agent(
        binding, "list_dir",
        {"path": path, "cwd": binding.remote_path},
        timeout=15, operation="list_dir", detail=path, key_info=key_info,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    entries = result.get("entries", [])
    await _log_operation(
        binding, "list_dir", path, key_info["key_id"],
        duration_ms=duration_ms, stdout_len=len(entries),
    )

    return {
        "entries": entries,
        "count": len(entries),
        "path": result.get("path", path),
    }


# ── New tools (stat / file_exists / mkdir / delete / move / copy / glob / grep) ──


@mcp.tool()
async def remote_stat(project_id: str, path: str) -> dict:
    """Return metadata for a remote path: type / size / mtime / mode.

    Returns ``{exists: false, type: null}`` for missing paths instead of
    raising — callers can use this as a cheap existence check before a
    full ``remote_read_file``.
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)

    result = await _send_to_agent(
        binding, "stat",
        {"path": path, "cwd": binding.remote_path},
        timeout=10, operation="stat", detail=path, key_info=key_info,
    )
    await _log_operation(binding, "stat", path, key_info["key_id"])
    return result


@mcp.tool()
async def remote_file_exists(project_id: str, path: str) -> dict:
    """Cheap existence check. Returns ``{exists, type}`` only.

    Equivalent to ``remote_stat`` but returns the minimal subset; useful
    when you only need a yes/no answer (e.g. before creating a file).
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)

    result = await _send_to_agent(
        binding, "stat",
        {"path": path, "cwd": binding.remote_path},
        timeout=10, operation="stat", detail=path, key_info=key_info,
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
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)

    result = await _send_to_agent(
        binding, "mkdir",
        {"path": path, "cwd": binding.remote_path, "parents": parents},
        timeout=10, operation="mkdir", detail=path, key_info=key_info,
    )
    await _log_operation(binding, "mkdir", path, key_info["key_id"])
    return result


@mcp.tool()
async def remote_delete_file(
    project_id: str, path: str, recursive: bool = False
) -> dict:
    """Delete a file or directory on the remote machine.

    Directories require ``recursive=True``. Refuses to delete the
    workspace root.
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)

    result = await _send_to_agent(
        binding, "delete",
        {"path": path, "cwd": binding.remote_path, "recursive": recursive},
        timeout=30, operation="delete", detail=path, key_info=key_info,
    )
    await _log_operation(binding, "delete", path, key_info["key_id"])
    return result


@mcp.tool()
async def remote_move_file(
    project_id: str, src: str, dst: str, overwrite: bool = False
) -> dict:
    """Move/rename a file or directory on the remote machine."""
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(src)
    _validate_remote_path(dst)

    result = await _send_to_agent(
        binding, "move",
        {"src": src, "dst": dst, "cwd": binding.remote_path, "overwrite": overwrite},
        timeout=30, operation="move", detail=f"{src} -> {dst}", key_info=key_info,
    )
    await _log_operation(binding, "move", f"{src} -> {dst}", key_info["key_id"])
    return result


@mcp.tool()
async def remote_copy_file(
    project_id: str, src: str, dst: str, overwrite: bool = False
) -> dict:
    """Copy a file or directory on the remote machine."""
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(src)
    _validate_remote_path(dst)

    result = await _send_to_agent(
        binding, "copy",
        {"src": src, "dst": dst, "cwd": binding.remote_path, "overwrite": overwrite},
        timeout=60, operation="copy", detail=f"{src} -> {dst}", key_info=key_info,
    )
    await _log_operation(binding, "copy", f"{src} -> {dst}", key_info["key_id"])
    return result


@mcp.tool()
async def remote_glob(
    project_id: str, pattern: str, path: str = "."
) -> dict:
    """Find files matching a glob pattern under ``path`` on the remote machine.

    Pattern semantics match Python's ``pathlib.Path.glob`` (use ``**`` for
    recursive descent). Results are sorted by mtime descending — the most
    recently modified files come first, matching Claude Code's local Glob
    tool behavior.

    Args:
        project_id: Project ID or project name
        pattern: Glob pattern, e.g. ``**/*.py`` or ``src/**/*.tsx``
        path: Base directory (relative to workspace, default ``.``)
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)
    _validate_remote_pattern(pattern, kind="glob")

    result = await _send_to_agent(
        binding, "glob",
        {"pattern": pattern, "path": path, "cwd": binding.remote_path},
        timeout=30, operation="glob", detail=f"{pattern} @ {path}", key_info=key_info,
    )
    await _log_operation(binding, "glob", f"{pattern} @ {path}", key_info["key_id"])
    return result


@mcp.tool()
async def remote_grep(
    project_id: str,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    case_insensitive: bool = False,
    max_results: int = 200,
    respect_gitignore: bool = False,
) -> dict:
    """Search for ``pattern`` (regex) inside files under ``path``.

    Args:
        project_id: Project ID or project name
        pattern: Regular expression to search for
        path: Base directory (relative to workspace, default ``.``)
        glob: Optional file-name glob filter (e.g. ``*.py``)
        case_insensitive: Match without regard to letter case
        max_results: Maximum number of matches to return (1-2000)
        respect_gitignore: When the agent has ripgrep available, honor
            ``.gitignore`` / ``.ignore`` files. Default ``False`` for
            backwards compatibility with the Python fallback. The Python
            fallback never reads gitignore regardless of this flag.

    Returns matches as ``[{file, line, text}]`` sorted by ``(file, line)``.
    The result also includes ``files_scanned``, ``files_skipped_binary``,
    ``files_skipped_large``, and ``engine`` (``"ripgrep"`` or ``"python"``)
    for visibility into how the search ran.

    Engine: if the remote agent has ``ripgrep`` (``rg``) installed, it
    is used and is 10–100× faster than the Python fallback. Otherwise
    a pure-Python implementation is used.

    The agent automatically skips:
    - Heavy/vendored directories (.git, node_modules, .venv, venv, env,
      __pycache__, .pytest_cache, .mypy_cache, .ruff_cache, dist, build,
      target, .next, .nuxt, .cache, .idea, .vscode, coverage, …)
    - Files with binary extensions (images, videos, archives, fonts,
      compiled binaries, .pdf, .docx, …)
    - Files larger than 10 MB
    - Files whose first 8 KB contain a NUL byte (binary heuristic)
    """
    key_info = await authenticate()
    binding = await _resolve_binding(project_id, key_info)
    _validate_remote_path(path)
    _validate_remote_pattern(pattern, kind="grep")

    if not isinstance(max_results, int) or max_results < 1 or max_results > 2000:
        raise ToolError("max_results must be an integer between 1 and 2000")

    payload: dict = {
        "pattern": pattern,
        "path": path,
        "cwd": binding.remote_path,
        "case_insensitive": case_insensitive,
        "max_results": max_results,
        "respect_gitignore": respect_gitignore,
    }
    if glob:
        payload["glob"] = glob

    result = await _send_to_agent(
        binding, "grep", payload, timeout=60,
        operation="grep", detail=f"{pattern} @ {path}", key_info=key_info,
    )
    await _log_operation(binding, "grep", f"{pattern} @ {path}", key_info["key_id"])
    return result
