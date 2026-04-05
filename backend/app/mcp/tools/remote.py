"""MCP tools for remote command execution and file operations via connected agents."""

import logging
import time

from fastmcp.exceptions import ToolError

from ...models.terminal import RemoteExecLog, RemoteWorkspace, TerminalAgent
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _resolve_project_id

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_TIMEOUT = 300  # seconds


def _get_agent_manager():
    """Lazy import to avoid circular dependency."""
    from ...api.v1.endpoints.terminal import AgentOfflineError, CommandTimeoutError, agent_manager
    return agent_manager, AgentOfflineError, CommandTimeoutError


async def _resolve_workspace(project_id: str, scopes: list[str]) -> RemoteWorkspace:
    """Resolve project_id to a RemoteWorkspace, with access checks."""
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, scopes)

    workspace = await RemoteWorkspace.find_one({"project_id": project_id})
    if not workspace:
        raise ToolError(f"No remote workspace configured for project {project_id}")
    return workspace


async def _log_operation(
    workspace: RemoteWorkspace,
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
            workspace_id=str(workspace.id),
            agent_id=workspace.agent_id,
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


@mcp.tool()
async def list_remote_agents() -> list[dict]:
    """List registered remote agents and their connection status.

    Returns a list of agents with id, name, hostname, os_type, is_online, and workspace count.
    """
    await authenticate()
    manager, _, _ = _get_agent_manager()

    agents = await TerminalAgent.find_all().to_list()
    result = []
    for a in agents:
        aid = str(a.id)
        ws_count = await RemoteWorkspace.find({"agent_id": aid}).count()
        result.append({
            "id": aid,
            "name": a.name,
            "hostname": a.hostname,
            "os_type": a.os_type,
            "is_online": manager.is_connected(aid),
            "workspace_count": ws_count,
            "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        })
    return result


@mcp.tool()
async def remote_exec(
    project_id: str,
    command: str,
    timeout: int = 60,
) -> dict:
    """Execute a shell command on the remote machine linked to this project.

    The command runs in the project's configured remote directory (cwd).
    Supports any shell command including git, docker, npm, etc.

    Args:
        project_id: Project ID or project name
        command: Shell command to execute
        timeout: Execution timeout in seconds (1-300, default 60)
    """
    key_info = await authenticate()
    workspace = await _resolve_workspace(project_id, key_info["project_scopes"])
    manager, AgentOfflineError, CommandTimeoutError = _get_agent_manager()

    timeout = max(1, min(timeout, MAX_TIMEOUT))
    t0 = time.monotonic()

    try:
        result = await manager.send_request(
            workspace.agent_id,
            "exec",
            {"command": command, "cwd": workspace.remote_path, "timeout": timeout},
            timeout=timeout + 5,  # grace period for agent-side timeout
        )
    except AgentOfflineError:
        await _log_operation(workspace, "exec", command, key_info["key_id"], error="agent_offline")
        raise ToolError("Agent is offline")
    except CommandTimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _log_operation(workspace, "exec", command, key_info["key_id"],
                             duration_ms=duration_ms, error="timeout")
        raise ToolError(f"Command timed out after {timeout}s")
    except RuntimeError as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await _log_operation(workspace, "exec", command, key_info["key_id"],
                             duration_ms=duration_ms, error=str(e))
        raise ToolError(str(e))

    duration_ms = int((time.monotonic() - t0) * 1000)
    await _log_operation(
        workspace, "exec", command, key_info["key_id"],
        duration_ms=duration_ms,
        exit_code=result.get("exit_code"),
        stdout_len=len(result.get("stdout", "")),
        stderr_len=len(result.get("stderr", "")),
    )

    return {
        "exit_code": result.get("exit_code", -1),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "duration_ms": duration_ms,
    }


@mcp.tool()
async def remote_read_file(
    project_id: str,
    path: str,
) -> dict:
    """Read a file on the remote machine linked to this project.

    Path is relative to the project's remote directory, or absolute.

    Args:
        project_id: Project ID or project name
        path: File path (relative to project remote_path, or absolute)
    """
    key_info = await authenticate()
    workspace = await _resolve_workspace(project_id, key_info["project_scopes"])
    manager, AgentOfflineError, CommandTimeoutError = _get_agent_manager()

    t0 = time.monotonic()

    try:
        result = await manager.send_request(
            workspace.agent_id,
            "read_file",
            {"path": path, "cwd": workspace.remote_path},
            timeout=30,
        )
    except AgentOfflineError:
        await _log_operation(workspace, "read_file", path, key_info["key_id"], error="agent_offline")
        raise ToolError("Agent is offline")
    except CommandTimeoutError:
        await _log_operation(workspace, "read_file", path, key_info["key_id"], error="timeout")
        raise ToolError("Read file timed out")
    except RuntimeError as e:
        await _log_operation(workspace, "read_file", path, key_info["key_id"], error=str(e))
        raise ToolError(str(e))

    duration_ms = int((time.monotonic() - t0) * 1000)
    content = result.get("content", "")
    await _log_operation(
        workspace, "read_file", path, key_info["key_id"],
        duration_ms=duration_ms, stdout_len=len(content),
    )

    return {
        "content": content,
        "size": result.get("size", len(content)),
        "path": result.get("path", path),
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

    Args:
        project_id: Project ID or project name
        path: File path (relative to project remote_path, or absolute)
        content: File content to write
    """
    key_info = await authenticate()
    workspace = await _resolve_workspace(project_id, key_info["project_scopes"])
    manager, AgentOfflineError, CommandTimeoutError = _get_agent_manager()

    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ToolError(f"Content too large (max {MAX_FILE_BYTES // 1024 // 1024} MB)")

    t0 = time.monotonic()

    try:
        result = await manager.send_request(
            workspace.agent_id,
            "write_file",
            {"path": path, "cwd": workspace.remote_path, "content": content},
            timeout=30,
        )
    except AgentOfflineError:
        await _log_operation(workspace, "write_file", path, key_info["key_id"], error="agent_offline")
        raise ToolError("Agent is offline")
    except CommandTimeoutError:
        await _log_operation(workspace, "write_file", path, key_info["key_id"], error="timeout")
        raise ToolError("Write file timed out")
    except RuntimeError as e:
        await _log_operation(workspace, "write_file", path, key_info["key_id"], error=str(e))
        raise ToolError(str(e))

    duration_ms = int((time.monotonic() - t0) * 1000)
    await _log_operation(
        workspace, "write_file", path, key_info["key_id"],
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

    Args:
        project_id: Project ID or project name
        path: Directory path (relative to project remote_path, or absolute; default ".")
    """
    key_info = await authenticate()
    workspace = await _resolve_workspace(project_id, key_info["project_scopes"])
    manager, AgentOfflineError, CommandTimeoutError = _get_agent_manager()

    t0 = time.monotonic()

    try:
        result = await manager.send_request(
            workspace.agent_id,
            "list_dir",
            {"path": path, "cwd": workspace.remote_path},
            timeout=15,
        )
    except AgentOfflineError:
        await _log_operation(workspace, "list_dir", path, key_info["key_id"], error="agent_offline")
        raise ToolError("Agent is offline")
    except CommandTimeoutError:
        await _log_operation(workspace, "list_dir", path, key_info["key_id"], error="timeout")
        raise ToolError("List directory timed out")
    except RuntimeError as e:
        await _log_operation(workspace, "list_dir", path, key_info["key_id"], error=str(e))
        raise ToolError(str(e))

    duration_ms = int((time.monotonic() - t0) * 1000)
    entries = result.get("entries", [])
    await _log_operation(
        workspace, "list_dir", path, key_info["key_id"],
        duration_ms=duration_ms, stdout_len=len(entries),
    )

    return {
        "entries": entries,
        "count": len(entries),
        "path": result.get("path", path),
    }
