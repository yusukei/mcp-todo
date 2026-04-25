#!/usr/bin/env python3
"""MCP Todo — Remote Terminal Agent.

Connects to the central server via WebSocket and handles remote
command execution, file operations, and Web Terminal PTY sessions.

Usage:
    python main.py --url wss://example.com/api/v1/workspaces/agent/ws --token ta_xxx
    python main.py --config ~/.mcp-workspace/config.json
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import platform
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from self_update import (
    UpdateError,
    __version__,
    apply_update,
    cleanup_old_files,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("workspace-agent")

IS_WINDOWS = sys.platform == "win32"

MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_DIR_ENTRIES = 1000
MAX_GLOB_RESULTS = 1000
MAX_GREP_RESULTS_DEFAULT = 200
# ripgrep is REQUIRED for handle_grep. Detected at startup and resolved
# to an absolute path so subprocess invocation does not depend on the
# agent process's CWD. If ripgrep is missing, handle_grep returns an
# error rather than silently falling back to a slow Python loop — a
# Python implementation simply isn't fast enough on real-world repos.
def _detect_ripgrep() -> str | None:
    found = shutil.which("rg")
    if not found:
        return None
    # Resolve to absolute path so a relative result like ".\\rg.EXE"
    # doesn't break when subprocess working directory differs.
    return os.path.abspath(found)


RG_PATH: str | None = _detect_ripgrep()


def _compute_host_id() -> str:
    """Stable per-host identifier (sha256 hex, 16 chars).

    Used by the supervisor / backend to join the supervisor record
    with the agent record running on the same physical machine. We
    derive it from ``platform.node()`` because that is good enough
    for the single-host personal-use deployment; renaming the host
    intentionally invalidates the binding, which is the correct
    behavior.
    """
    import hashlib

    raw = f"{platform.node()}::{sys.platform}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _detect_shells() -> list[str]:
    """Report every shell we can actually launch.

    Windows: cmd.exe always; PowerShell flavors when present; and — new
    — common bash locations (Git for Windows, msys2, WSL wrapper) so
    the backend can route ``shell="bash"`` requests to a real POSIX
    environment without user-installed tooling.

    POSIX: whichever of zsh/bash/sh exist under ``/bin``.
    """
    if IS_WINDOWS:
        shells: list[str] = []
        comspec = os.environ.get("COMSPEC", r"C:\Windows\system32\cmd.exe")
        if os.path.exists(comspec):
            shells.append(comspec)

        for ps in [
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            r"C:\Program Files\PowerShell\7\pwsh.exe",
        ]:
            if os.path.exists(ps):
                shells.append(ps)

        # Git for Windows, msys2, and a bundled busybox fallback. First
        # existing match wins — callers only need one bash entry.
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get(
            "ProgramFiles(x86)", r"C:\Program Files (x86)",
        )
        local_appdata = os.environ.get(
            "LOCALAPPDATA",
            os.path.expanduser(r"~\AppData\Local"),
        )
        agent_dir = os.path.dirname(os.path.abspath(__file__))
        bash_candidates = [
            # Git for Windows — the most common install path, widely
            # present on dev machines with no extra work from the user.
            os.path.join(program_files, "Git", "bin", "bash.exe"),
            os.path.join(program_files, "Git", "usr", "bin", "bash.exe"),
            os.path.join(program_files_x86, "Git", "bin", "bash.exe"),
            # msys2
            r"C:\msys64\usr\bin\bash.exe",
            r"C:\msys2\usr\bin\bash.exe",
            # User-scope installs
            os.path.join(local_appdata, "Programs", "Git", "bin", "bash.exe"),
            # Busybox-w32 shipped alongside the agent binary (Phase 3
            # fallback). Kept last so real bash wins when both exist.
            os.path.join(agent_dir, "busybox.exe"),
        ]
        for candidate in bash_candidates:
            if os.path.exists(candidate):
                shells.append(candidate)
                break  # one bash entry is enough

        return shells or [comspec]
    else:
        shells = []
        for sh in ["/bin/zsh", "/bin/bash", "/bin/sh"]:
            if os.path.exists(sh):
                shells.append(sh)
        return shells or ["/bin/sh"]


# Sentinel meaning "use asyncio.create_subprocess_shell" (platform default).
_DEFAULT_SHELL = object()


def _resolve_shell_exec(hint: str) -> object | list[str] | None:
    """Map a ``shell=`` hint to an argv prefix for ``create_subprocess_exec``.

    Returns:
        ``_DEFAULT_SHELL`` sentinel → use ``create_subprocess_shell``
        ``list[str]`` → argv prefix (e.g. ``[bash_path, "-c"]``)
        ``None`` → requested shell is unavailable on this agent
    """
    if hint in ("", "default"):
        return _DEFAULT_SHELL
    shells = _detect_shells()

    def _match_stem(candidates: tuple[str, ...]) -> str | None:
        for s in shells:
            stem = os.path.splitext(os.path.basename(s))[0].lower()
            if stem in candidates:
                return s
        return None

    if hint in ("bash", "sh"):
        # busybox-w32 only dispatches sub-applets; it needs the ``sh``
        # applet name before the script.
        bash_path = _match_stem(("bash", "sh"))
        if bash_path and os.path.basename(bash_path).lower().startswith("busybox"):
            return [bash_path, "sh", "-c"]
        if bash_path:
            return [bash_path, "-c"]
        return None

    if hint in ("pwsh", "powershell"):
        ps_path = _match_stem(("pwsh", "powershell"))
        if ps_path:
            return [ps_path, "-NoProfile", "-Command"]
        return None

    if hint == "cmd":
        cmd_path = _match_stem(("cmd",))
        if cmd_path:
            return [cmd_path, "/c"]
        return None

    # Unknown hint — explicit error rather than silent fallback.
    return None


def _resolve_safe_path(path: str, cwd: str | None) -> str:
    """Resolve a user-supplied path against cwd, ensuring it stays inside cwd.

    Both relative and absolute inputs are normalized via realpath, and the
    result must be a descendant of (or equal to) the realpath of ``cwd``.

    Raises:
        ValueError: when ``cwd`` is missing/invalid or when the resolved path
            escapes ``cwd`` (path traversal).
    """
    if not cwd:
        raise ValueError("cwd is required")
    # Reject NUL bytes / control chars early — defense in depth
    if "\x00" in path:
        raise ValueError("Invalid path: contains NUL byte")
    base = os.path.realpath(cwd)
    if not os.path.isdir(base):
        raise ValueError(f"Working directory does not exist: {cwd}")
    candidate = path if os.path.isabs(path) else os.path.join(base, path)
    resolved = os.path.realpath(candidate)
    try:
        common = os.path.commonpath([resolved, base])
    except ValueError:
        # Different drives on Windows, or mixed path types
        raise ValueError("Path traversal not allowed")
    if common != base:
        raise ValueError("Path traversal not allowed")
    return resolved


def _resolve_safe_dir(path: str, cwd: str) -> str:
    """Like ``_resolve_safe_path`` but allows the path to not exist yet.

    Used by mkdir / write_file / move destinations where the target may
    be created. The parent directory must exist (or will be created),
    and the resolved path still has to live under ``cwd``.
    """
    if not cwd:
        raise ValueError("cwd is required")
    if "\x00" in path:
        raise ValueError("Invalid path: contains NUL byte")
    base = os.path.realpath(cwd)
    if not os.path.isdir(base):
        raise ValueError(f"Working directory does not exist: {cwd}")
    candidate = path if os.path.isabs(path) else os.path.join(base, path)
    # Use abspath (not realpath) since the path may not exist yet, then
    # symlink-resolve the parent. The final path must still be inside base.
    resolved_parent = os.path.realpath(os.path.dirname(os.path.abspath(candidate)) or base)
    final = os.path.join(resolved_parent, os.path.basename(candidate))
    try:
        common = os.path.commonpath([resolved_parent, base])
    except ValueError:
        raise ValueError("Path traversal not allowed")
    if common != base:
        raise ValueError("Path traversal not allowed")
    return final


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess safely across platforms."""
    try:
        if IS_WINDOWS:
            proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                proc.kill()
    except ProcessLookupError:
        pass


def _truncate_with_flag(data: bytes, limit: int) -> tuple[str, bool, int]:
    """Decode bytes to UTF-8 (with replacement), truncating to ``limit``.

    Returns ``(decoded_text, was_truncated, total_bytes)`` so the caller
    can tell whether the original output exceeded the buffer.
    """
    total = len(data)
    truncated = total > limit
    if truncated:
        data = data[:limit]
    return data.decode("utf-8", errors="replace"), truncated, total


# ── Background job store ────────────────────────────────────

import uuid as _uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class _BackgroundJob:
    job_id: str
    command: str
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    started_at: str = ""
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None


_bg_jobs: dict[str, _BackgroundJob] = {}
_MAX_BG_JOBS = 64


async def _run_bg_job(job: _BackgroundJob, msg: dict) -> None:
    """Run a command in the background and store results in the job object."""
    command = msg.get("command", "")
    base_cwd = msg.get("cwd")
    cwd_override = msg.get("cwd_override")
    extra_env = msg.get("env") or {}
    timeout = min(msg.get("timeout", 3600), 3600)

    effective_cwd = base_cwd
    if cwd_override:
        try:
            effective_cwd = _resolve_safe_path(cwd_override, base_cwd)
        except ValueError as e:
            job.exit_code = -1
            job.stderr = f"Invalid cwd_override: {e}"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            return
        if not os.path.isdir(effective_cwd):
            job.exit_code = -1
            job.stderr = f"cwd_override is not a directory: {cwd_override}"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            return

    proc_env: dict[str, str] | None = None
    if extra_env and isinstance(extra_env, dict):
        proc_env = os.environ.copy()
        for k, v in extra_env.items():
            if isinstance(k, str) and isinstance(v, str):
                proc_env[k] = v

    start_ms = int(time.time() * 1000)
    try:
        kwargs: dict = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": effective_cwd,
        }
        if proc_env is not None:
            kwargs["env"] = proc_env
        if not IS_WINDOWS:
            kwargs["preexec_fn"] = os.setsid

        proc = await asyncio.create_subprocess_shell(command, **kwargs)
        job.pid = proc.pid

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            _kill_process(proc)
            stdout, stderr = await proc.communicate()
            out_text, out_trunc, out_total = _truncate_with_flag(stdout, MAX_OUTPUT_BYTES)
            err_text, err_trunc, err_total = _truncate_with_flag(stderr, MAX_OUTPUT_BYTES)
            job.exit_code = -1
            job.stdout = out_text
            job.stderr = err_text + f"\n[timeout after {timeout}s]"
            job.stdout_truncated = out_trunc
            job.stderr_truncated = err_trunc
            job.stdout_total_bytes = out_total
            job.stderr_total_bytes = err_total
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.duration_ms = int(time.time() * 1000) - start_ms
            return

        out_text, out_trunc, out_total = _truncate_with_flag(stdout, MAX_OUTPUT_BYTES)
        err_text, err_trunc, err_total = _truncate_with_flag(stderr, MAX_OUTPUT_BYTES)
        job.exit_code = proc.returncode
        job.stdout = out_text
        job.stderr = err_text
        job.stdout_truncated = out_trunc
        job.stderr_truncated = err_trunc
        job.stdout_total_bytes = out_total
        job.stderr_total_bytes = err_total
    except Exception as e:
        job.exit_code = -1
        job.stderr = str(e)

    job.completed_at = datetime.now(timezone.utc).isoformat()
    job.duration_ms = int(time.time() * 1000) - start_ms


async def handle_exec_background(msg: dict) -> dict:
    """Start a command in the background and return a job_id immediately."""
    # Evict oldest completed jobs if at capacity
    if len(_bg_jobs) >= _MAX_BG_JOBS:
        completed = [jid for jid, j in _bg_jobs.items() if j.completed_at]
        for jid in completed[:len(completed) // 2 + 1]:
            del _bg_jobs[jid]

    job_id = _uuid.uuid4().hex[:12]
    job = _BackgroundJob(
        job_id=job_id,
        command=msg.get("command", "")[:200],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    _bg_jobs[job_id] = job

    # Fire and forget
    asyncio.create_task(_run_bg_job(job, msg))

    return {
        "job_id": job_id,
        "status": "running",
        "started_at": job.started_at,
    }


async def handle_exec_status(msg: dict) -> dict:
    """Return the current status of a background job."""
    job_id = msg.get("job_id", "")
    job = _bg_jobs.get(job_id)
    if job is None:
        return {"error": f"Job not found: {job_id}", "status": "not_found"}

    result: dict = {
        "job_id": job.job_id,
        "status": "completed" if job.completed_at else "running",
        "command": job.command,
        "started_at": job.started_at,
    }
    if job.completed_at:
        result.update({
            "exit_code": job.exit_code,
            "stdout": job.stdout,
            "stderr": job.stderr,
            "stdout_truncated": job.stdout_truncated,
            "stderr_truncated": job.stderr_truncated,
            "stdout_total_bytes": job.stdout_total_bytes,
            "stderr_total_bytes": job.stderr_total_bytes,
            "completed_at": job.completed_at,
            "duration_ms": job.duration_ms,
        })
    elif job.pid:
        result["pid"] = job.pid
    return result


# ── Handlers ────────────────────────────────────────────────


async def handle_exec(msg: dict) -> dict:
    """Execute a shell command and return stdout/stderr.

    Optional fields:
    - ``cwd_override``: subdirectory inside the workspace to run in
    - ``env``: extra environment variables to merge with the agent's env
    - ``shell``: ``"default"`` (native), ``"bash"`` / ``"sh"`` (POSIX via
      Git/msys2/busybox), ``"cmd"`` (Windows cmd.exe), ``"pwsh"`` /
      ``"powershell"`` (PowerShell). Unknown shells return a clear error.

    The response always includes ``stdout_truncated`` / ``stderr_truncated``
    flags + total byte counts so callers can detect output that exceeded
    the agent buffer.
    """
    command = msg.get("command", "")
    base_cwd = msg.get("cwd")
    cwd_override = msg.get("cwd_override")
    extra_env = msg.get("env") or {}
    timeout = min(msg.get("timeout", 60), 3600)
    shell_hint = (msg.get("shell") or "default").lower()

    if base_cwd and not os.path.isdir(base_cwd):
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Working directory does not exist: {base_cwd}",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout_total_bytes": 0,
            "stderr_total_bytes": 0,
        }

    # Resolve cwd_override against the workspace base, with path-traversal
    # protection. Without an override, run in the workspace root.
    effective_cwd = base_cwd
    if cwd_override:
        try:
            resolved = _resolve_safe_path(cwd_override, base_cwd)
        except ValueError as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Invalid cwd_override: {e}",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "stdout_total_bytes": 0,
                "stderr_total_bytes": 0,
            }
        if not os.path.isdir(resolved):
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"cwd_override is not a directory: {cwd_override}",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "stdout_total_bytes": 0,
                "stderr_total_bytes": 0,
            }
        effective_cwd = resolved

    # Build environment by merging the agent's env with the override.
    # Reject non-string keys/values to keep subprocess happy.
    proc_env: dict[str, str] | None = None
    if extra_env:
        if not isinstance(extra_env, dict):
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "env must be an object of string→string",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "stdout_total_bytes": 0,
                "stderr_total_bytes": 0,
            }
        proc_env = os.environ.copy()
        for k, v in extra_env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": "env keys/values must be strings",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                    "stdout_total_bytes": 0,
                    "stderr_total_bytes": 0,
                }
            proc_env[k] = v

    try:
        kwargs: dict = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": effective_cwd,
        }
        if proc_env is not None:
            kwargs["env"] = proc_env
        if not IS_WINDOWS:
            kwargs["preexec_fn"] = os.setsid

        # Shell selection — ``default`` keeps the historical behaviour
        # (asyncio.create_subprocess_shell → cmd on Windows, /bin/sh on
        # POSIX). Explicit shells route through create_subprocess_exec
        # so we can point at any interpreter (bash, pwsh, busybox).
        shell_exec = _resolve_shell_exec(shell_hint)
        if shell_exec is None:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": (
                    f"shell={shell_hint!r} not available on this agent "
                    f"(available: {[os.path.basename(s) for s in _detect_shells()]})"
                ),
                "stdout_truncated": False,
                "stderr_truncated": False,
                "stdout_total_bytes": 0,
                "stderr_total_bytes": 0,
            }
        if shell_exec is _DEFAULT_SHELL:
            proc = await asyncio.create_subprocess_shell(command, **kwargs)
        else:
            proc = await asyncio.create_subprocess_exec(
                *shell_exec, command, **kwargs,
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            _kill_process(proc)
            stdout, stderr = await proc.communicate()
            out_text, out_truncated, out_total = _truncate_with_flag(stdout, MAX_OUTPUT_BYTES)
            err_text, err_truncated, err_total = _truncate_with_flag(stderr, MAX_OUTPUT_BYTES)
            return {
                "exit_code": -1,
                "stdout": out_text,
                "stderr": err_text + f"\n[timeout after {timeout}s]",
                "stdout_truncated": out_truncated,
                "stderr_truncated": err_truncated,
                "stdout_total_bytes": out_total,
                "stderr_total_bytes": err_total,
            }

        out_text, out_truncated, out_total = _truncate_with_flag(stdout, MAX_OUTPUT_BYTES)
        err_text, err_truncated, err_total = _truncate_with_flag(stderr, MAX_OUTPUT_BYTES)
        return {
            "exit_code": proc.returncode,
            "stdout": out_text,
            "stderr": err_text,
            "stdout_truncated": out_truncated,
            "stderr_truncated": err_truncated,
            "stdout_total_bytes": out_total,
            "stderr_total_bytes": err_total,
        }

    except Exception as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout_total_bytes": 0,
            "stderr_total_bytes": 0,
        }


async def handle_read_file(msg: dict) -> dict:
    """Read a file and return its content.

    New optional fields:
    - ``offset`` (1-based line number) + ``limit`` (line count) for partial reads
    - ``encoding`` ('utf-8' default; 'binary' / 'base64' for binary mode)
    """
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    offset = msg.get("offset")
    limit = msg.get("limit")
    encoding = msg.get("encoding") or "utf-8"

    try:
        path = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"error": str(e)}

    def _read_text():
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return {"error": f"File too large: {size} bytes (max {MAX_FILE_BYTES // 1024 // 1024} MB)"}
        with open(path, "r", encoding=encoding, errors="replace") as f:
            if offset is None and limit is None:
                content = f.read()
                total_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
                return {
                    "content": content,
                    "size": size,
                    "path": path,
                    "encoding": encoding,
                    "is_binary": False,
                    "total_lines": total_lines,
                    "truncated": False,
                }
            # Line-range read
            lines = f.readlines()
            total_lines = len(lines)
            start = max(0, (offset or 1) - 1)
            end = total_lines if limit is None else min(total_lines, start + max(0, int(limit)))
            slice_text = "".join(lines[start:end])
            return {
                "content": slice_text,
                "size": size,
                "path": path,
                "encoding": encoding,
                "is_binary": False,
                "total_lines": total_lines,
                "truncated": end < total_lines,
                "offset": start + 1,
                "limit": end - start,
            }

    def _read_binary():
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return {"error": f"File too large: {size} bytes (max {MAX_FILE_BYTES // 1024 // 1024} MB)"}
        with open(path, "rb") as f:
            data = f.read()
        return {
            "content": base64.b64encode(data).decode("ascii"),
            "size": size,
            "path": path,
            "encoding": "base64",
            "is_binary": True,
            "total_lines": 0,
            "truncated": False,
        }

    try:
        if encoding in ("binary", "base64"):
            result = await asyncio.to_thread(_read_binary)
        else:
            result = await asyncio.to_thread(_read_text)
        return {**result}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except LookupError as e:  # unknown codec
        return {"error": f"Unknown encoding: {e}"}
    except Exception as e:
        return {"error": str(e)}


async def handle_write_file(msg: dict) -> dict:
    """Write content to a file."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    content = msg.get("content", "")

    try:
        path = _resolve_safe_dir(path, cwd)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    def _write():
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            return {"success": False, "error": f"Content too large: {len(data)} bytes (max {MAX_FILE_BYTES // 1024 // 1024} MB)"}
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "bytes_written": len(data), "path": path}

    try:
        result = await asyncio.to_thread(_write)
        return {**result}
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_edit_file(msg: dict) -> dict:
    """Apply a string-replacement edit to a file.

    Finds ``old_string`` in the file and replaces it with ``new_string``.
    When ``replace_all`` is true every occurrence is replaced; otherwise
    exactly one unique match is required (ambiguous matches are rejected).
    """
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    old_string: str = msg.get("old_string", "")
    new_string: str = msg.get("new_string", "")
    replace_all: bool = msg.get("replace_all", False)

    if not old_string:
        return {"success": False, "error": "old_string is required"}
    if old_string == new_string:
        return {"success": False, "error": "old_string and new_string must differ"}

    try:
        path = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    def _edit():
        if not os.path.isfile(path):
            return {"success": False, "error": f"File not found: {path}"}
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return {
                "success": False,
                "error": f"File too large: {size} bytes (max {MAX_FILE_BYTES // 1024 // 1024} MB)",
            }
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_string)
        if count == 0:
            return {"success": False, "error": "old_string not found in file"}
        if not replace_all and count > 1:
            return {
                "success": False,
                "error": f"old_string is not unique — found {count} occurrences. "
                "Provide more surrounding context to make it unique, or set replace_all=true.",
            }

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        replacements = count if replace_all else 1
        return {
            "success": True,
            "path": path,
            "replacements": replacements,
        }

    try:
        return await asyncio.to_thread(_edit)
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except UnicodeDecodeError:
        return {"success": False, "error": f"File is not valid UTF-8: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_list_dir(msg: dict) -> dict:
    """List directory contents."""
    path = msg.get("path", ".")
    cwd = msg.get("cwd")

    try:
        path = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"error": str(e)}

    def _list():
        entries = []
        with os.scandir(path) as scanner:
            for entry in scanner:
                if len(entries) >= MAX_DIR_ENTRIES:
                    break
                try:
                    stat = entry.stat(follow_symlinks=False)
                    entries.append({
                        "name": entry.name,
                        "type": "dir" if entry.is_dir(follow_symlinks=False) else
                                "symlink" if entry.is_symlink() else "file",
                        "size": stat.st_size if not entry.is_dir(follow_symlinks=False) else 0,
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    })
                except (OSError, PermissionError):
                    entries.append({"name": entry.name, "type": "unknown", "size": 0, "modified": ""})
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return entries

    try:
        entries = await asyncio.to_thread(_list)
        return {"entries": entries, "path": path}
    except FileNotFoundError:
        return {"error": f"Directory not found: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except Exception as e:
        return {"error": str(e)}


# ── New handlers (stat / file_exists / mkdir / delete / move / copy / glob / grep) ──


async def handle_stat(msg: dict) -> dict:
    """Return file metadata (size, mtime, type, mode) or `exists=False`."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")

    try:
        resolved = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"error": str(e)}

    def _stat():
        # The envelope is built by the dispatcher under a nested
        # ``payload`` key (see _RunHandler in this file), so an inner
        # ``type`` key is now safe — it cannot shadow the envelope.
        if not os.path.exists(resolved) and not os.path.islink(resolved):
            return {"exists": False, "type": None, "path": resolved}
        st = os.lstat(resolved)
        if os.path.islink(resolved):
            ftype = "symlink"
        elif os.path.isdir(resolved):
            ftype = "directory"
        else:
            ftype = "file"
        return {
            "exists": True,
            "type": ftype,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "mode": oct(st.st_mode & 0o777),
            "path": resolved,
        }

    try:
        return {**(await asyncio.to_thread(_stat))}
    except Exception as e:
        return {"error": str(e)}


async def handle_mkdir(msg: dict) -> dict:
    """Create a directory (parents=True by default)."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    parents = msg.get("parents", True)

    try:
        resolved = _resolve_safe_dir(path, cwd)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    def _mkdir():
        try:
            if parents:
                os.makedirs(resolved, exist_ok=True)
            else:
                os.mkdir(resolved)
            return {"success": True, "path": resolved, "created": True}
        except FileExistsError:
            if parents:
                # exist_ok=True handles this, but be defensive
                return {"success": True, "path": resolved, "created": False}
            return {"success": False, "error": f"Already exists: {resolved}"}

    try:
        return {**(await asyncio.to_thread(_mkdir))}
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {resolved}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_delete(msg: dict) -> dict:
    """Delete a file or directory (recursive opt-in for directories)."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    recursive = bool(msg.get("recursive", False))

    try:
        resolved = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Refuse to delete the workspace root itself.
    base = os.path.realpath(cwd) if cwd else None
    if base and resolved == base:
        return {"success": False,
                "error": "Refusing to delete workspace root"}

    def _delete():
        # ``type`` here is safe to use as a payload field because the
        # dispatcher nests payload under a separate key (see _RunHandler).
        if os.path.islink(resolved) or os.path.isfile(resolved):
            os.remove(resolved)
            return {"success": True, "path": resolved, "type": "file"}
        if os.path.isdir(resolved):
            if not recursive:
                return {"success": False, "error": "Directory delete requires recursive=True"}
            shutil.rmtree(resolved)
            return {"success": True, "path": resolved, "type": "directory"}
        return {"success": False, "error": f"Path not found: {resolved}"}

    try:
        return {**(await asyncio.to_thread(_delete))}
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {resolved}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_move(msg: dict) -> dict:
    """Move/rename a file or directory inside the workspace."""
    src = msg.get("src", "")
    dst = msg.get("dst", "")
    cwd = msg.get("cwd")
    overwrite = bool(msg.get("overwrite", False))

    try:
        src_resolved = _resolve_safe_path(src, cwd)
        dst_resolved = _resolve_safe_dir(dst, cwd)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    def _move():
        if not os.path.exists(src_resolved) and not os.path.islink(src_resolved):
            return {"success": False, "error": f"Source not found: {src_resolved}"}
        if os.path.exists(dst_resolved):
            if not overwrite:
                return {"success": False, "error": f"Destination exists: {dst_resolved}"}
            if os.path.isdir(dst_resolved) and not os.path.islink(dst_resolved):
                shutil.rmtree(dst_resolved)
            else:
                os.remove(dst_resolved)
        parent = os.path.dirname(dst_resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(src_resolved, dst_resolved)
        return {"success": True, "src": src_resolved, "dst": dst_resolved}

    try:
        return {**(await asyncio.to_thread(_move))}
    except PermissionError as e:
        return {"success": False, "error": f"Permission denied: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_copy(msg: dict) -> dict:
    """Copy a file or directory inside the workspace."""
    src = msg.get("src", "")
    dst = msg.get("dst", "")
    cwd = msg.get("cwd")
    overwrite = bool(msg.get("overwrite", False))

    try:
        src_resolved = _resolve_safe_path(src, cwd)
        dst_resolved = _resolve_safe_dir(dst, cwd)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    def _copy():
        if not os.path.exists(src_resolved) and not os.path.islink(src_resolved):
            return {"success": False, "error": f"Source not found: {src_resolved}"}
        if os.path.exists(dst_resolved) and not overwrite:
            return {"success": False, "error": f"Destination exists: {dst_resolved}"}
        parent = os.path.dirname(dst_resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.isdir(src_resolved) and not os.path.islink(src_resolved):
            if os.path.exists(dst_resolved):
                shutil.rmtree(dst_resolved)
            shutil.copytree(src_resolved, dst_resolved)
        else:
            shutil.copy2(src_resolved, dst_resolved)
        return {"success": True, "src": src_resolved, "dst": dst_resolved}

    try:
        return {**(await asyncio.to_thread(_copy))}
    except PermissionError as e:
        return {"success": False, "error": f"Permission denied: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def handle_glob(msg: dict) -> dict:
    """Find files matching a glob pattern under ``path``.

    Pattern semantics match ``pathlib.Path.rglob`` for ``**`` (any depth)
    and ``Path.glob`` for ``*``. The result is sorted by mtime descending
    so the most recently modified matches come first.
    """
    pattern = msg.get("pattern", "")
    path = msg.get("path", ".")
    cwd = msg.get("cwd")

    if not pattern:
        return {"error": "pattern is required"}

    try:
        base_dir = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"error": str(e)}

    def _glob():
        base = Path(base_dir)
        if not base.is_dir():
            return {"error": f"Not a directory: {base_dir}"}
        results: list[dict] = []
        # `**` patterns use rglob; everything else uses glob.
        try:
            iterator = base.glob(pattern)
        except (NotImplementedError, ValueError) as e:
            return {"error": f"Invalid glob pattern: {e}"}
        for entry in iterator:
            if len(results) >= MAX_GLOB_RESULTS:
                break
            try:
                # Skip directories — return only files (matches Claude Code Glob)
                if not entry.is_file():
                    continue
                st = entry.stat()
                results.append({
                    "path": str(entry),
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                })
            except (OSError, PermissionError):
                continue
        results.sort(key=lambda e: e["mtime"], reverse=True)
        return {"matches": results, "count": len(results), "base": base_dir, "truncated": len(results) >= MAX_GLOB_RESULTS}

    try:
        result = await asyncio.to_thread(_glob)
        return {**result}
    except Exception as e:
        return {"error": str(e)}


# Directories that are universally heavy / vendored / generated.
# We pass these to ripgrep as `-g '!<dir>'` exclusions when running with
# --no-ignore so it doesn't walk into .venv / node_modules / .git on
# real-world repositories.
GREP_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "bower_components",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", "target", "out",
    ".next", ".nuxt", ".cache", ".parcel-cache",
    ".idea", ".vscode",
    "coverage", ".nyc_output",
})


class RipgrepError(Exception):
    """Raised when ripgrep cannot be executed or returns an error."""


def _decode_rg_text_field(field: dict | None) -> str:
    """Decode a ripgrep --json text field.

    ripgrep emits ``{"text": "..."}`` for valid UTF-8 and
    ``{"bytes": "<base64>"}`` when the bytes are not valid UTF-8.
    Decoding errors propagate; the caller (or test) will see them.
    """
    if not field:
        return ""
    if "text" in field:
        return field["text"]
    if "bytes" in field:
        # Use errors="replace" only for the byte→str step: invalid UTF-8
        # is the *expected* reason ripgrep used the bytes form, so it's
        # not an error to handle. base64 decoding itself will raise on
        # malformed input.
        return base64.b64decode(field["bytes"]).decode("utf-8", errors="replace")
    return ""


async def _grep_with_rg(
    *,
    base_dir: str,
    pattern: str,
    glob_filter: str | None,
    case_insensitive: bool,
    max_results: int,
    respect_gitignore: bool,
    context_lines: int = 0,
) -> dict:
    """Run ripgrep and translate its --json output to our schema.

    Raises ``RipgrepError`` on launch failure, timeout, or non-zero/non-1
    exit. ``json.JSONDecodeError`` propagates if ripgrep emits malformed
    JSON (which would indicate a bug — we want to know).
    """
    t0 = time.perf_counter()

    cmd: list[str] = [
        RG_PATH,  # type: ignore[list-item]
        "--json",
        "--no-messages",
        "--no-config",
        "--max-count", str(max_results),
        "--max-filesize", "10M",
    ]
    if not respect_gitignore:
        # Without --no-ignore, ripgrep skips gitignored files which may
        # not match our intent. With --no-ignore, ripgrep would happily
        # walk into .venv / node_modules / .git, so we explicitly exclude
        # our skip list via glob filters.
        cmd.append("--no-ignore")
        for skip_dir in GREP_SKIP_DIRS:
            cmd += ["-g", f"!{skip_dir}", "-g", f"!**/{skip_dir}/**"]
    if context_lines > 0:
        cmd += ["-C", str(context_lines)]
    if case_insensitive:
        cmd.append("-i")
    if glob_filter:
        cmd += ["-g", glob_filter]
    cmd += ["-e", pattern, "--", base_dir]

    logger.info(
        "[grep] launching ripgrep: rg=%s pattern=%r base=%s glob=%r ci=%s "
        "max=%d gitignore=%s argc=%d",
        RG_PATH, pattern, base_dir, glob_filter, case_insensitive,
        max_results, respect_gitignore, len(cmd),
    )
    logger.debug("[grep] full argv: %r", cmd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        logger.exception("[grep] failed to launch ripgrep at %s", RG_PATH)
        raise RipgrepError(f"failed to launch ripgrep ({RG_PATH}): {e}") from e

    logger.info("[grep] ripgrep started (pid=%s); awaiting communicate()", proc.pid)

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError as e:
        elapsed = time.perf_counter() - t0
        logger.error(
            "[grep] ripgrep TIMED OUT after %.1fs (pid=%s, base_dir=%s) — killing",
            elapsed, proc.pid, base_dir,
        )
        proc.kill()
        raise RipgrepError(f"ripgrep timed out after 30s (base_dir={base_dir})") from e

    elapsed = time.perf_counter() - t0
    logger.info(
        "[grep] ripgrep finished: pid=%s exit=%s elapsed=%.3fs "
        "stdout=%dB stderr=%dB",
        proc.pid, proc.returncode, elapsed, len(stdout), len(stderr),
    )

    # ripgrep exit codes:
    #   0 = matches found
    #   1 = no matches (not an error)
    #   2 = error
    if proc.returncode not in (0, 1):
        err_text = stderr.decode("utf-8", errors="replace").strip()
        logger.error("[grep] ripgrep exited %s: %s", proc.returncode, err_text)
        raise RipgrepError(
            f"ripgrep exited {proc.returncode}: {err_text or 'unknown error'}"
        )

    matches: list[dict] = []
    # When context_lines > 0, ripgrep emits "context" events interleaved
    # with "match" events.  We buffer context lines that precede a match
    # and, once a match is seen, attach trailing context lines to it.
    # A "begin" event (new file) or another "match" event flushes the
    # trailing-context buffer.
    pending_before: list[dict] = []  # context lines waiting for the next match
    files_scanned = 0
    truncated = False
    summary_matches: int | None = None

    for raw_line in stdout.split(b"\n"):
        if not raw_line.strip():
            continue
        # JSONDecodeError propagates intentionally — malformed output
        # from ripgrep is a bug we must surface, not silently skip.
        evt = json.loads(raw_line)
        etype = evt.get("type")
        data = evt.get("data") or {}
        if etype == "context":
            ctx_entry = {
                "line": data.get("line_number"),
                "text": _decode_rg_text_field(data.get("lines")).rstrip("\n")[:500],
            }
            if matches and _decode_rg_text_field(data.get("path")) == matches[-1]["file"]:
                # Trailing context for the previous match — but only
                # if it belongs to the same file.
                matches[-1].setdefault("context_after", []).append(ctx_entry)
            else:
                pending_before.append(ctx_entry)
        elif etype == "match":
            # Flush pending_before into this match's context_before,
            # and reset trailing context tracking.
            if len(matches) >= max_results:
                truncated = True
                pending_before.clear()
                continue
            file_path = _decode_rg_text_field(data.get("path"))
            line_no = data.get("line_number")
            text = _decode_rg_text_field(data.get("lines")).rstrip("\n")[:500]
            entry: dict = {
                "file": file_path,
                "line": line_no,
                "text": text,
            }
            if pending_before:
                entry["context_before"] = pending_before
                pending_before = []
            # When consecutive matches appear, the trailing context of
            # the previous match may actually be the leading context of
            # the next. Ripgrep handles this by NOT emitting separate
            # context events between adjacent matches — so we don't
            # need to move entries between matches.
            matches.append(entry)
        elif etype == "begin":
            # New file — reset leading-context buffer.
            pending_before.clear()
        elif etype == "end":
            files_scanned += 1
        elif etype == "summary":
            stats = data.get("stats") or {}
            summary_matches = stats.get("matches")

    # If summary reports more matches than we kept, ripgrep was truncated
    # by --max-count per file or we hit our own ceiling.
    if summary_matches is not None and summary_matches > len(matches):
        truncated = True

    matches.sort(key=lambda m: (m["file"], m["line"] or 0))
    logger.info(
        "[grep] parsed: matches=%d files_scanned=%d truncated=%s",
        len(matches), files_scanned, truncated,
    )
    return {
        "matches": matches,
        "count": len(matches),
        "files_scanned": files_scanned,
        "truncated": truncated,
        "engine": "ripgrep",
    }


async def handle_grep(msg: dict) -> dict:
    """Search for ``pattern`` (regex) inside files under ``path``.

    Requires ripgrep (``rg``) to be installed on the agent host. The
    Python fallback was removed because it was not fast enough on
    real-world repositories. If ripgrep is missing, this returns an
    error and the operator must install it.

    Optional file filter ``glob`` (e.g. ``*.py``) limits the file set.
    Returns at most ``max_results`` matches with file path + line number.

    Heavy / vendored directories (see ``GREP_SKIP_DIRS``) are pruned by
    passing ``-g '!<dir>'`` and ``-g '!**/<dir>/**'`` filters to ripgrep.
    Setting ``respect_gitignore=True`` lets ripgrep honor ``.gitignore``
    instead, in which case the explicit skip globs are not added.
    """
    request_id = msg["request_id"]
    logger.info("[grep] handle_grep called: request_id=%s RG_PATH=%s", request_id, RG_PATH)

    if RG_PATH is None:
        logger.error("[grep] RG_PATH is None — returning install-hint error")
        return {
            "error": (
                "ripgrep (rg) is not installed on the agent host. "
                "Install it (macOS: brew install ripgrep | "
                "Debian/Ubuntu: apt install ripgrep | "
                "Windows: winget install BurntSushi.ripgrep.MSVC) "
                "and restart the agent."
            ),
        }

    pattern = msg.get("pattern", "")
    path = msg.get("path", ".")
    cwd = msg.get("cwd")
    glob_filter = msg.get("glob")
    case_insensitive = bool(msg.get("case_insensitive", False))
    respect_gitignore = bool(msg.get("respect_gitignore", False))
    try:
        context_lines = max(0, min(int(msg.get("context_lines", 0)), 20))
    except (TypeError, ValueError):
        context_lines = 0
    logger.info(
        "[grep] msg: pattern=%r path=%r cwd=%r glob=%r ci=%s gitignore=%s ctx=%d",
        pattern, path, cwd, glob_filter, case_insensitive, respect_gitignore,
        context_lines,
    )
    try:
        max_results_raw = int(msg.get("max_results", MAX_GREP_RESULTS_DEFAULT))
    except (TypeError, ValueError):
        return {"error": "max_results must be an integer"}
    max_results = max(1, min(max_results_raw, 2000))

    if not pattern:
        return {"error": "pattern is required"}

    try:
        base_dir = _resolve_safe_path(path, cwd)
    except ValueError as e:
        # Known rejection: we return a structured error to the MCP
        # layer rather than raising, so the full traceback must be
        # captured here (``logger.exception``) — otherwise the
        # operator only sees the message string.
        logger.exception("[grep] path validation failed")
        return {"error": str(e)}
    logger.info("[grep] resolved base_dir=%s", base_dir)

    if not os.path.exists(base_dir):
        logger.error("[grep] base_dir does not exist: %s", base_dir)
        return {"error": f"Not a directory: {base_dir}"}

    try:
        result = await _grep_with_rg(
            base_dir=base_dir,
            pattern=pattern,
            glob_filter=glob_filter,
            case_insensitive=case_insensitive,
            max_results=max_results,
            respect_gitignore=respect_gitignore,
            context_lines=context_lines,
        )
    except RipgrepError as e:
        # Known failure modes (launch failure / timeout / non-zero exit)
        # surface as a structured error to the MCP layer. We swallow
        # the exception here because the caller wants a structured
        # error dict — but we must keep the traceback visible in
        # logs (CLAUDE.md "No error hiding").
        logger.exception("[grep] RipgrepError")
        return {"error": str(e)}
    # Any other exception (JSONDecodeError, programmer bugs, …) intentionally
    # propagates. The agent's outer dispatcher will log a stack trace and
    # the operator will see exactly what went wrong instead of a vague
    # "ripgrep failed" message.

    logger.info("[grep] returning %d matches for request_id=%s", result.get("count", 0), request_id)
    return {**result}


_HANDLERS = {
    "exec": handle_exec,
    "exec_background": handle_exec_background,
    "exec_status": handle_exec_status,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "edit_file": handle_edit_file,
    "list_dir": handle_list_dir,
    "stat": handle_stat,
    "mkdir": handle_mkdir,
    "delete": handle_delete,
    "move": handle_move,
    "copy": handle_copy,
    "glob": handle_glob,
    "grep": handle_grep,
}


# Map handler request type → response envelope type. Most are
# ``<request>_result`` but read_file / list_dir use legacy names that
# are kept for stability with the public MCP API surface.
_RESPONSE_TYPE_FOR = {
    "exec": "exec_result",
    "exec_background": "exec_background_result",
    "exec_status": "exec_status_result",
    "read_file": "file_content",
    "write_file": "write_result",
    "edit_file": "edit_result",
    "list_dir": "dir_listing",
    "stat": "stat_result",
    "mkdir": "mkdir_result",
    "delete": "delete_result",
    "move": "move_result",
    "copy": "copy_result",
    "glob": "glob_result",
    "grep": "grep_result",
}


class PtySession:
    """Cross-platform PTY session for the Web Terminal feature.

    Unix uses stdlib ``pty``+``fcntl``; Windows uses ``pywinpty``. Reads
    run in an executor so they do not block the asyncio loop.
    """

    def __init__(self) -> None:
        self._fd: int | None = None
        self._pid: int | None = None
        self._winpty = None
        self._alive = False

    async def spawn(self, shell: str, cols: int, rows: int) -> None:
        if IS_WINDOWS:
            await self._spawn_windows(shell, cols, rows)
        else:
            await self._spawn_unix(shell, cols, rows)
        self._alive = True

    async def _spawn_unix(self, shell: str, cols: int, rows: int) -> None:
        import fcntl
        import pty
        import struct
        import termios

        pid, fd = pty.openpty()
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        child_pid = os.fork()
        if child_pid == 0:
            os.close(pid)
            os.setsid()
            import tty
            tty.setraw(fd)
            fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
            os.dup2(fd, 0)
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            if fd > 2:
                os.close(fd)
            os.execvpe(shell, [shell], env)
        else:
            os.close(fd)
            self._fd = pid
            self._pid = child_pid

    async def _spawn_windows(self, shell: str, cols: int, rows: int) -> None:
        from winpty import PtyProcess  # type: ignore[import-not-found]

        self._winpty = PtyProcess.spawn(shell, dimensions=(rows, cols))

    async def read(self) -> bytes | None:
        if not self._alive:
            return None
        if IS_WINDOWS:
            return await self._read_windows()
        return await self._read_unix()

    async def _read_unix(self) -> bytes | None:
        if self._fd is None:
            return None
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, os.read, self._fd, 4096)
            return data if data else None
        except OSError:
            self._alive = False
            return None

    def _read_windows_sync(self) -> bytes | None:
        try:
            data = self._winpty.read(4096)  # type: ignore[union-attr]
            if not data:
                return None
            return data.encode("utf-8", errors="replace") if isinstance(data, str) else data
        except EOFError:
            return None
        except Exception:
            logger.exception("pywinpty read raised")
            return None

    async def _read_windows(self) -> bytes | None:
        if self._winpty is None:
            return None
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._read_windows_sync)
        if data is None:
            self._alive = False
        return data

    async def write(self, data: str) -> None:
        if IS_WINDOWS:
            if self._winpty:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._winpty.write, data)
        else:
            if self._fd is not None:
                loop = asyncio.get_event_loop()
                payload = data.encode() if isinstance(data, str) else data
                await loop.run_in_executor(None, os.write, self._fd, payload)

    async def resize(self, cols: int, rows: int) -> None:
        if IS_WINDOWS:
            if self._winpty:
                try:
                    self._winpty.setwinsize(rows, cols)
                except Exception:
                    logger.exception("PTY resize (windows) failed")
        else:
            if self._fd is not None:
                import fcntl
                import struct
                import termios
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                try:
                    fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
                except OSError:
                    logger.exception("PTY resize (TIOCSWINSZ) failed")
            if self._pid:
                try:
                    os.kill(self._pid, signal.SIGWINCH)
                except (OSError, AttributeError):
                    pass

    async def terminate(self) -> int:
        self._alive = False
        exit_code = -1
        if IS_WINDOWS:
            if self._winpty:
                try:
                    self._winpty.terminate()
                    exit_code = 0
                except Exception:
                    logger.exception("PTY terminate (windows) failed")
                self._winpty = None
        else:
            if self._pid:
                try:
                    os.kill(self._pid, signal.SIGTERM)
                    _, status = os.waitpid(self._pid, 0)
                    exit_code = (
                        os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
                    )
                except (ChildProcessError, OSError):
                    pass
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            self._pid = None
        return exit_code

    @property
    def alive(self) -> bool:
        if IS_WINDOWS:
            if self._winpty:
                try:
                    return bool(self._winpty.isalive())
                except Exception:
                    return False
            return False
        if self._pid:
            try:
                pid, _ = os.waitpid(self._pid, os.WNOHANG)
                return pid == 0
            except ChildProcessError:
                return False
        return False


class PtyManager:
    """Multiplexes browser-driven PTY sessions over the agent WebSocket.

    Each session is keyed by a browser-supplied ``session_id``. Output
    is pushed back as ``terminal_output`` envelopes; the backend routes
    bytes to the originating browser by ``session_id``.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._reader_tasks: dict[str, asyncio.Task] = {}

    async def handle_terminal_create(self, msg: dict, send) -> None:
        request_id = msg.get("request_id")
        payload = msg.get("payload") or {}
        session_id = payload.get("session_id")
        if not session_id:
            await self._send_envelope(
                send, "terminal_create_result", request_id,
                {"success": False, "error": "session_id required"},
            )
            return
        if session_id in self._sessions:
            await self._send_envelope(
                send, "terminal_create_result", request_id,
                {"success": False, "error": "session_id already exists"},
            )
            return

        shell_hint = payload.get("shell") or ""
        cols = int(payload.get("cols", 120))
        rows = int(payload.get("rows", 40))
        detected = _detect_shells()
        if shell_hint and os.path.exists(shell_hint):
            shell = shell_hint
        elif detected:
            shell = detected[0]
        else:
            shell = "/bin/sh"

        session = PtySession()
        try:
            await session.spawn(shell, cols, rows)
        except Exception as e:
            logger.exception("PTY spawn failed for %s", session_id)
            await self._send_envelope(
                send, "terminal_create_result", request_id,
                {"success": False, "error": f"spawn failed: {e}"},
            )
            return

        self._sessions[session_id] = session
        task = asyncio.create_task(self._reader_loop(session_id, session, send))
        self._reader_tasks[session_id] = task

        await self._send_envelope(
            send, "terminal_create_result", request_id,
            {"success": True, "session_id": session_id, "shell": shell},
        )

    async def handle_terminal_input(self, msg: dict) -> None:
        payload = msg.get("payload") or {}
        session_id = payload.get("session_id")
        data = payload.get("data", "")
        session = self._sessions.get(session_id) if session_id else None
        if session and session.alive:
            await session.write(data)

    async def handle_terminal_resize(self, msg: dict) -> None:
        payload = msg.get("payload") or {}
        session_id = payload.get("session_id")
        cols = int(payload.get("cols", 120))
        rows = int(payload.get("rows", 40))
        session = self._sessions.get(session_id) if session_id else None
        if session:
            await session.resize(cols, rows)

    async def handle_terminal_close(self, msg: dict) -> None:
        payload = msg.get("payload") or {}
        session_id = payload.get("session_id")
        if not session_id:
            return
        task = self._reader_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _reader_loop(self, session_id: str, session: PtySession, send) -> None:
        try:
            while session.alive:
                data = await session.read()
                if data is None:
                    break
                text = data.decode("utf-8", errors="replace")
                await self._send_envelope(
                    send, "terminal_output", None,
                    {"session_id": session_id, "data": text},
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PTY reader loop crashed for %s", session_id)
        finally:
            self._sessions.pop(session_id, None)
            self._reader_tasks.pop(session_id, None)
            try:
                exit_code = await session.terminate()
            except Exception:
                logger.exception("PTY terminate failed for %s", session_id)
                exit_code = -1
            try:
                await self._send_envelope(
                    send, "terminal_exit", None,
                    {"session_id": session_id, "exit_code": exit_code},
                )
            except Exception:
                logger.exception(
                    "Failed to send terminal_exit for %s", session_id,
                )

    @staticmethod
    async def _send_envelope(
        send, type_: str, request_id: str | None, payload: dict,
    ) -> None:
        envelope: dict = {"type": type_, "payload": payload}
        if request_id is not None:
            envelope["request_id"] = request_id
        await send(json.dumps(envelope))

    def cancel_all(self) -> None:
        for task in list(self._reader_tasks.values()):
            if not task.done():
                task.cancel()


# ── Agent ────────────────────────────────────────────────────


class WorkspaceAgent:
    def __init__(self, server_url: str, token: str):
        self.server_url = server_url
        self.token = token
        self._ws = None
        self._running = True
        self._agent_id: str | None = None
        self._pty_manager = PtyManager()
        self._send_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()
        # ── Diagnostic state (per-connection; reset in _connect) ──
        # Used to classify disconnects as network-idle vs server-initiated
        # vs app error when ConnectionClosedError surfaces without detail.
        self._connected_at: float | None = None
        self._last_recv_at: float | None = None
        self._last_ping_sent_at: float | None = None
        self._last_pong_at: float | None = None
        self._recv_count: int = 0

    async def _safe_send(self, data: str) -> None:
        """Send data on WebSocket with lock to prevent frame interleaving.

        Send failures here mean the WS is dead — the outer ``_connect``
        loop will detect this and reconnect, so we intentionally do not
        re-raise. But we DO log with full traceback (CLAUDE.md "No
        error hiding") so operators can see *why* the send failed
        instead of just a terse warning.
        """
        ws = self._ws
        if not ws:
            return
        async with self._send_lock:
            try:
                await ws.send(data)
            except Exception:
                logger.exception(
                    "Send failed (agent_id=%s); connection will be retried",
                    self._agent_id,
                )

    async def _heartbeat_loop(self, ws) -> None:
        """Send a ping every 30 s to keep the connection alive.

        The server responds with a pong and refreshes its Redis TTL +
        last_seen_at. If the send raises, the connection is dead — the
        outer _connect loop will detect the broken ws and reconnect.
        We intentionally do not raise here: the message-loop's ``async
        for raw in ws`` will surface the disconnect on the next
        iteration and trigger cleanup + reconnect.
        """
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send(json.dumps({"type": "ping"}))
                self._last_ping_sent_at = time.monotonic()
                logger.debug(
                    "Heartbeat ping sent (agent_id=%s)", self._agent_id,
                )
            except Exception:
                logger.exception(
                    "Heartbeat ping failed (agent_id=%s); "
                    "connection will be retried",
                    self._agent_id,
                )
                return

    def _spawn_task(self, coro) -> None:
        """Spawn a background task and track it for cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def run(self) -> None:
        backoff = 1
        max_backoff = 60

        # Imported here (not at module top) to match the lazy import
        # pattern used by _connect and avoid a hard import-time dep.
        from websockets.exceptions import ConnectionClosed

        while self._running:
            try:
                await self._connect()
                backoff = 1
            except ConnectionClosed:
                # Expected: peer disconnected. _log_disconnect already
                # emitted structured diagnostics (close_code, idle times,
                # network_drop_hint). Keep this at WARNING — the full
                # traceback isn't useful here since the interesting state
                # is already logged, and ``logger.exception`` on every
                # NAT-idle disconnect produces noisy duplicates.
                logger.warning(
                    "Connection closed — will reconnect (agent_id=%s)",
                    self._agent_id,
                )
            except Exception:
                # Unexpected failures: auth token invalid, TLS mismatch,
                # DNS, websockets handshake errors, etc. Full traceback
                # is essential here because _log_disconnect did not run.
                # CLAUDE.md forbids ``logger.error(..., e)`` without
                # exc_info — use ``logger.exception``.
                logger.exception("Connection failed (unexpected)")

            if not self._running:
                break

            logger.info("Reconnecting in %ds...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    def _log_disconnect(self, exc, ws) -> None:
        """Log structured diagnostics for a WebSocket disconnect.

        Classifies disconnects by combining ``ConnectionClosed.rcvd`` /
        ``sent`` (were close frames exchanged?) with ``ws.close_code`` /
        ``close_reason`` and idle timings from the heartbeat loop.
        """
        now = time.monotonic()
        uptime = now - self._connected_at if self._connected_at else None
        idle_recv = (
            now - self._last_recv_at if self._last_recv_at else None
        )
        since_ping = (
            now - self._last_ping_sent_at
            if self._last_ping_sent_at else None
        )
        since_pong = (
            now - self._last_pong_at if self._last_pong_at else None
        )
        rcvd = getattr(exc, "rcvd", None)
        sent = getattr(exc, "sent", None)
        rcvd_summary = (
            f"code={rcvd.code} reason={rcvd.reason!r}" if rcvd else "None"
        )
        sent_summary = (
            f"code={sent.code} reason={sent.reason!r}" if sent else "None"
        )
        # When both rcvd and sent are None, no close handshake happened
        # — strong hint this is a network drop (NAT/LB idle timeout,
        # Wi-Fi disconnect, proxy reset) rather than a graceful close.
        network_drop_hint = (rcvd is None and sent is None)
        logger.warning(
            "WebSocket disconnected: type=%s close_code=%s close_reason=%r "
            "rcvd=[%s] sent=[%s] uptime=%.1fs recv_count=%d "
            "idle_since_recv=%s idle_since_ping_sent=%s "
            "idle_since_pong=%s network_drop_hint=%s",
            type(exc).__name__,
            getattr(ws, "close_code", None),
            getattr(ws, "close_reason", None),
            rcvd_summary,
            sent_summary,
            uptime if uptime is not None else -1.0,
            self._recv_count,
            f"{idle_recv:.1f}s" if idle_recv is not None else "n/a",
            f"{since_ping:.1f}s" if since_ping is not None else "n/a",
            f"{since_pong:.1f}s" if since_pong is not None else "n/a",
            network_drop_hint,
        )

    def _log_connection_summary(self, ws) -> None:
        """Emit a one-line summary of the just-ended connection."""
        if self._connected_at is None:
            return
        uptime = time.monotonic() - self._connected_at
        logger.info(
            "Connection ended: agent_id=%s uptime=%.1fs recv_count=%d "
            "close_code=%s close_reason=%r",
            self._agent_id,
            uptime,
            self._recv_count,
            getattr(ws, "close_code", None),
            getattr(ws, "close_reason", None),
        )

    async def _connect(self) -> None:
        import websockets
        from websockets.exceptions import ConnectionClosed

        logger.info("Connecting to %s", self.server_url)

        # Reset per-connection diagnostic state.
        self._connected_at = time.monotonic()
        self._last_recv_at = None
        self._last_ping_sent_at = None
        self._last_pong_at = None
        self._recv_count = 0

        async with websockets.connect(
            self.server_url,
            ping_interval=None,  # Disable library-level ping; we send
                                 # application-level pings in _heartbeat_loop.
            max_size=10 * 1024 * 1024,  # 10 MB max message
        ) as ws:
            self._ws = ws

            # ── Auth via first message ──
            await ws.send(json.dumps({
                "type": "auth",
                "token": self.token,
            }))

            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") != "auth_ok":
                error = msg.get("message", "Authentication failed")
                logger.error("Auth failed: %s", error)
                raise ConnectionRefusedError(error)

            self._agent_id = msg.get("agent_id")
            logger.info("Authenticated as agent %s", self._agent_id)

            await ws.send(json.dumps({
                "type": "agent_info",
                "hostname": platform.node(),
                "host_id": _compute_host_id(),
                "os": sys.platform,
                "shells": _detect_shells(),
                "agent_version": __version__,
            }))

            # ── Heartbeat task ──
            # Send a ping every 30 s so the server can refresh its Redis
            # TTL and update last_seen_at. The server echoes back a pong.
            # Using an application-level ping instead of the websockets
            # library's built-in ping_interval avoids the race where
            # uvicorn doesn't respond to WS-frame-level ping frames fast
            # enough under load, causing spurious ping_timeout disconnects.
            self._spawn_task(self._heartbeat_loop(ws))

            # ── Message loop ──
            try:
                async for raw in ws:
                    self._last_recv_at = time.monotonic()
                    self._recv_count += 1
                    if not self._running:
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        # Unexpected: backend is the only WS peer and it
                        # always sends valid JSON frames. Surface the
                        # malformed frame loudly so we notice protocol
                        # drift instead of silently dropping it.
                        preview = (
                            raw[:200] if isinstance(raw, str)
                            else raw[:200].decode("utf-8", errors="replace")
                        )
                        logger.warning(
                            "Agent: dropped non-JSON frame from server: %r",
                            preview,
                        )
                        continue
                    if msg.get("type") == "pong":
                        self._last_pong_at = time.monotonic()
                        logger.debug(
                            "Heartbeat pong received (agent_id=%s)",
                            self._agent_id,
                        )
                    await self._handle_message(msg)
            except ConnectionClosed as exc:
                # websockets raises this with structured close metadata
                # — log it so we can classify the disconnect type:
                #   code=1006, rcvd/sent=None  → abrupt TCP drop (network/NAT idle)
                #   code=1011                  → server-side unhandled error
                #   code=1012                  → server restart / service restart
                #   code=1000 / 1001           → graceful close
                # Both the exception object and ws.close_code/reason are
                # inspected because ``rcvd``/``sent`` show which side sent
                # the close frame — when both are None we never completed
                # the close handshake (true network drop).
                self._log_disconnect(exc, ws)
                # Re-raise so run()'s outer loop triggers reconnect/backoff.
                raise
            finally:
                # Emit a connection summary regardless of how we exited
                # (clean close, ConnectionClosed, or other exception) so
                # operators have a per-session record for triage.
                self._log_connection_summary(ws)
                # Cleanup on disconnect: tear down active PTY sessions
                self._ws = None
                self._pty_manager.cancel_all()
                # Cancel background tasks
                for task in list(self._background_tasks):
                    task.cancel()
                self._background_tasks.clear()

    async def _handle_message(self, msg: dict) -> None:
        msg_type = msg.get("type")

        if msg_type == "shutdown":
            # Graceful shutdown requested by the supervisor (or another
            # operator with admin access to the agent's WebSocket). The
            # supervisor uses this as the first step of its kill chain
            # — see Rust Supervisor design doc §6.2 — before falling
            # back to CTRL_BREAK_EVENT and finally TerminateJobObject.
            logger.info("Shutdown requested over WS")
            self._spawn_task(self.shutdown())
            return

        if msg_type == "terminal_create":
            self._spawn_task(
                self._pty_manager.handle_terminal_create(msg, self._safe_send)
            )
            return

        if msg_type == "terminal_input":
            self._spawn_task(self._pty_manager.handle_terminal_input(msg))
            return

        if msg_type == "terminal_resize":
            self._spawn_task(self._pty_manager.handle_terminal_resize(msg))
            return

        if msg_type == "terminal_close":
            self._spawn_task(self._pty_manager.handle_terminal_close(msg))
            return

        # Regular handlers: also run as tasks to avoid blocking the loop.
        #
        # The new envelope format (introduced 2026-04-08 along with the
        # shadowing fix) carries request data in a nested ``payload`` key
        # so envelope fields (``type``, ``request_id``) cannot be shadowed
        # by user data. Unwrap it here so handlers continue to see a flat
        # dict — the per-handler signature is unchanged, only the wire
        # format around it.
        handler = _HANDLERS.get(msg_type) if isinstance(msg_type, str) else None
        if handler and isinstance(msg_type, str):
            inbound_payload = msg.get("payload") or {}
            if not isinstance(inbound_payload, dict):
                logger.warning(
                    "[dispatch] %s: ``payload`` is %s, not dict — ignoring",
                    msg_type, type(inbound_payload).__name__,
                )
                inbound_payload = {}
            synthetic = {**inbound_payload, "request_id": msg.get("request_id")}
            self._spawn_task(self._run_handler(handler, synthetic, msg_type))
            return

        if msg_type == "update_available":
            self._spawn_task(self._handle_update_available(msg))
            return

        if msg_type == "pong":
            # Server echoed our heartbeat ping — nothing to do.
            pass

    async def _run_handler(self, handler, msg: dict, msg_type: str) -> None:
        """Run a request/response handler as a background task.

        Handlers return *just* the payload dict (the inner data). The
        dispatcher wraps it in the envelope here. This is the architectural
        guarantee that handler code can never accidentally shadow envelope
        fields like ``type`` or ``request_id`` — those keys are owned by
        the dispatcher, full stop.
        """
        request_id = msg.get("request_id")
        response_type = _RESPONSE_TYPE_FOR.get(msg_type, f"{msg_type}_result")

        try:
            inner = await handler(msg)
            if inner is None:
                inner = {}
            envelope = {
                "type": response_type,
                "request_id": request_id,
                "payload": inner,
            }
            try:
                payload_str = json.dumps(envelope)
            except (TypeError, ValueError) as e:
                logger.exception(
                    "[dispatch] FAILED to serialize response for %s/%s: %s",
                    msg_type, request_id, e,
                )
                payload_str = json.dumps({
                    "type": response_type,
                    "request_id": request_id,
                    "payload": {"error": f"response serialization failed: {e}"},
                })
            logger.info(
                "[dispatch] sending response: type=%s req=%s bytes=%d",
                response_type, request_id, len(payload_str),
            )
            await self._safe_send(payload_str)
            logger.info(
                "[dispatch] response sent: type=%s req=%s",
                response_type, request_id,
            )
        except Exception as e:
            # Full stack trace so operators can see WHERE the handler died,
            # not just the exception message.
            logger.exception("[dispatch] Handler error for %s/%s: %s",
                             msg_type, request_id, e)
            if request_id:
                await self._safe_send(json.dumps({
                    "type": response_type,
                    "request_id": request_id,
                    "payload": {"error": f"{type(e).__name__}: {e}"},
                }))

    async def _handle_update_available(self, msg: dict) -> None:
        """Apply a server-pushed update and hand off to the new binary.

        All blocking I/O (HTTP download, file rename, sleep, spawn) is
        delegated to a worker thread so the WebSocket message loop
        keeps draining other messages while the update is in flight.
        On success the agent shuts itself down — the spawned child
        will reconnect with the new version.

        Race condition note: the spawned child connects to the server
        almost immediately, which causes the server to call
        old_ws.close(1012) on *this* process's WebSocket. That triggers
        ``_connect()``'s finally block, which cancels all
        ``_background_tasks``.  If this coroutine is still awaiting
        ``asyncio.to_thread(apply_update, ...)`` at that moment, it
        gets a CancelledError — and ``shutdown()`` is never called, so
        ``_running`` stays True and the old process enters an infinite
        reconnect loop.

        Fix:
          1. Remove this task from ``_background_tasks`` up-front so the
             finally block cannot cancel it mid-update.
          2. Set ``_running = False`` synchronously (no await) the
             instant ``apply_update`` returns, before any subsequent
             await point — guaranteeing the run() loop exits even if
             a CancelledError fires at the next await.
        """
        # (Fix 1) Detach from background_tasks so _connect()'s finally
        # block won't cancel this task when the WebSocket disconnects
        # after the new process takes the connection slot.
        if current_task := asyncio.current_task():
            self._background_tasks.discard(current_task)

        version = msg.get("version", "")
        download_url = msg.get("download_url", "")
        sha256 = msg.get("sha256", "")
        if not (version and download_url and sha256):
            logger.warning("update_available missing required fields, ignoring")
            return

        if not download_url.startswith(("http://", "https://")):
            download_url = self._absolute_download_url(download_url)

        logger.info("Update available: v%s (current %s)", version, __version__)
        try:
            old_path = await asyncio.to_thread(
                apply_update,
                download_url=download_url,
                sha256=sha256,
                version=version,
                token=self.token,
                restart_argv=sys.argv[1:],
            )
        except UpdateError:
            # UpdateError is a *known* failure mode from apply_update
            # (checksum mismatch, bad download, etc.). We still want
            # the full traceback in logs because operators need to see
            # which sub-step failed, not just the outer message.
            logger.exception("Update failed")
            return
        except Exception:
            logger.exception("Unexpected error during update")
            return

        # (Fix 2) Set _running = False synchronously — no await between
        # apply_update returning and this line — so that the run() loop
        # exits even if a CancelledError fires at the next await point.
        self._running = False

        logger.info(
            "Update applied (old binary preserved at %s); shutting down for handoff",
            old_path,
        )
        await self.shutdown()

    def _absolute_download_url(self, path: str) -> str:
        """Resolve a server-relative download path to a full HTTPS URL.

        The agent connects via wss://host/api/v1/workspaces/agent/ws but
        the release endpoint is exposed under https://host/api/v1/...
        so we keep the netloc and swap the scheme.
        """
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(self.server_url)
        scheme = "https" if parts.scheme in ("wss", "https") else "http"
        # Defensive: if the server forgot the leading slash the naive
        # urlunsplit would glue netloc and path together. Force it.
        if not path.startswith("/"):
            path = "/" + path
        return urlunsplit((scheme, parts.netloc, path, "", ""))

    async def shutdown(self) -> None:
        self._running = False
        self._pty_manager.cancel_all()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                # Shutdown path: the ws may already be gone. We still
                # want the traceback in logs so a stuck shutdown can
                # be diagnosed, but we do not re-raise — the process
                # is on its way out.
                logger.exception("shutdown: ws.close() failed")


# ── Config ───────────────────────────────────────────────────


def load_config(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        logger.error("Config file not found: %s", p)
        sys.exit(1)
    with open(p) as f:
        return json.load(f)


# ── Entry point ──────────────────────────────────────────────


def _log_grep_engine() -> None:
    """Log ripgrep detection result on startup.

    ripgrep is required for remote_grep. If it is missing the agent
    still starts (so other handlers keep working) but every grep
    request will return an error until ripgrep is installed.
    """
    if RG_PATH:
        logger.info("ripgrep detected at %s — remote_grep ready", RG_PATH)
    else:
        logger.error(
            "ripgrep (rg) NOT FOUND on PATH — remote_grep will return errors. "
            "Install it (macOS: brew install ripgrep | "
            "Debian/Ubuntu: apt install ripgrep | "
            "Windows: winget install BurntSushi.ripgrep.MSVC) "
            "and restart the agent."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Todo Remote Terminal Agent")
    parser.add_argument("--url", help="WebSocket server URL")
    parser.add_argument("--token", help="Agent authentication token")
    parser.add_argument("--config", help="Path to config JSON file", default="")
    args = parser.parse_args()

    _log_grep_engine()

    # Remove leftover .old/.new artifacts from a previous self-update.
    # This is a best-effort boot-time cleanup — a failure must not
    # prevent the agent from starting, but must leave a traceback in
    # logs (CLAUDE.md "No error hiding": ``logger.warning(..., e)``
    # without ``exc_info`` is forbidden).
    try:
        removed = cleanup_old_files()
        if removed:
            logger.info("Cleaned up %d stale update artifacts", removed)
    except Exception:
        logger.exception("Update cleanup failed")

    server_url = args.url
    token = args.token

    if args.config:
        cfg = load_config(args.config)
        server_url = server_url or cfg.get("server_url", "")
        token = token or cfg.get("token", "")

    if not server_url or not token:
        parser.error("--url and --token are required (or provide --config)")

    agent = WorkspaceAgent(server_url, token)

    async def _run():
        loop = asyncio.get_event_loop()

        def _shutdown(*_):
            logger.info("Shutting down...")
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(agent.shutdown()))

        if not IS_WINDOWS:
            loop.add_signal_handler(signal.SIGINT, _shutdown)
            loop.add_signal_handler(signal.SIGTERM, _shutdown)
        else:
            signal.signal(signal.SIGINT, _shutdown)
            signal.signal(signal.SIGTERM, _shutdown)

        await agent.run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    logger.info("Agent stopped")


if __name__ == "__main__":
    main()
