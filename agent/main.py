#!/usr/bin/env python3
"""MCP Todo — Remote Terminal Agent.

Connects to the central server via WebSocket and handles remote
command execution, file operations, and Claude Code chat sessions.

Usage:
    python main.py --url wss://example.com/api/v1/terminal/agent/ws --token ta_xxx
    python main.py --config ~/.mcp-terminal/config.json
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("terminal-agent")

IS_WINDOWS = sys.platform == "win32"

MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_DIR_ENTRIES = 1000
MAX_GLOB_RESULTS = 1000
MAX_GREP_RESULTS_DEFAULT = 200
CHAT_TIMEOUT = 30 * 60  # 30 minutes max per chat message

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


def _detect_shells() -> list[str]:
    if IS_WINDOWS:
        shells = []
        comspec = os.environ.get("COMSPEC", r"C:\Windows\system32\cmd.exe")
        if os.path.exists(comspec):
            shells.append(comspec)
        for ps in [
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            r"C:\Program Files\PowerShell\7\pwsh.exe",
        ]:
            if os.path.exists(ps):
                shells.append(ps)
        return shells or [comspec]
    else:
        shells = []
        for sh in ["/bin/zsh", "/bin/bash", "/bin/sh"]:
            if os.path.exists(sh):
                shells.append(sh)
        return shells or ["/bin/sh"]


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


# ── Handlers ────────────────────────────────────────────────


async def handle_exec(msg: dict) -> dict:
    """Execute a shell command and return stdout/stderr.

    New optional fields:
    - ``cwd_override``: subdirectory inside the workspace to run in
    - ``env``: extra environment variables to merge with the agent's env

    The response always includes ``stdout_truncated`` / ``stderr_truncated``
    flags + total byte counts so callers can detect output that exceeded
    the agent buffer.
    """
    command = msg.get("command", "")
    base_cwd = msg.get("cwd")
    cwd_override = msg.get("cwd_override")
    extra_env = msg.get("env") or {}
    timeout = min(msg.get("timeout", 60), 300)

    if base_cwd and not os.path.isdir(base_cwd):
        return {
            "type": "exec_result",
            "request_id": msg["request_id"],
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
                "type": "exec_result",
                "request_id": msg["request_id"],
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
                "type": "exec_result",
                "request_id": msg["request_id"],
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
                "type": "exec_result",
                "request_id": msg["request_id"],
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
                    "type": "exec_result",
                    "request_id": msg["request_id"],
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

        proc = await asyncio.create_subprocess_shell(command, **kwargs)

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
                "type": "exec_result",
                "request_id": msg["request_id"],
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
            "type": "exec_result",
            "request_id": msg["request_id"],
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
            "type": "exec_result",
            "request_id": msg["request_id"],
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
        return {"type": "file_content", "request_id": msg["request_id"], "error": str(e)}

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
        return {"type": "file_content", "request_id": msg["request_id"], **result}
    except FileNotFoundError:
        return {"type": "file_content", "request_id": msg["request_id"], "error": f"File not found: {path}"}
    except PermissionError:
        return {"type": "file_content", "request_id": msg["request_id"], "error": f"Permission denied: {path}"}
    except LookupError as e:  # unknown codec
        return {"type": "file_content", "request_id": msg["request_id"], "error": f"Unknown encoding: {e}"}
    except Exception as e:
        return {"type": "file_content", "request_id": msg["request_id"], "error": str(e)}


async def handle_write_file(msg: dict) -> dict:
    """Write content to a file."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    content = msg.get("content", "")

    try:
        path = _resolve_safe_dir(path, cwd)
    except ValueError as e:
        return {"type": "write_result", "request_id": msg["request_id"], "success": False, "error": str(e)}

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
        return {"type": "write_result", "request_id": msg["request_id"], **result}
    except PermissionError:
        return {"type": "write_result", "request_id": msg["request_id"], "success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"type": "write_result", "request_id": msg["request_id"], "success": False, "error": str(e)}


async def handle_list_dir(msg: dict) -> dict:
    """List directory contents."""
    path = msg.get("path", ".")
    cwd = msg.get("cwd")

    try:
        path = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"type": "dir_listing", "request_id": msg["request_id"], "error": str(e)}

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
        return {"type": "dir_listing", "request_id": msg["request_id"], "entries": entries, "path": path}
    except FileNotFoundError:
        return {"type": "dir_listing", "request_id": msg["request_id"], "error": f"Directory not found: {path}"}
    except PermissionError:
        return {"type": "dir_listing", "request_id": msg["request_id"], "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"type": "dir_listing", "request_id": msg["request_id"], "error": str(e)}


# ── New handlers (stat / file_exists / mkdir / delete / move / copy / glob / grep) ──


async def handle_stat(msg: dict) -> dict:
    """Return file metadata (size, mtime, type, mode) or `exists=False`."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")

    try:
        resolved = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"type": "stat_result", "request_id": msg["request_id"], "error": str(e)}

    def _stat():
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
        return {"type": "stat_result", "request_id": msg["request_id"], **(await asyncio.to_thread(_stat))}
    except Exception as e:
        return {"type": "stat_result", "request_id": msg["request_id"], "error": str(e)}


async def handle_mkdir(msg: dict) -> dict:
    """Create a directory (parents=True by default)."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    parents = msg.get("parents", True)

    try:
        resolved = _resolve_safe_dir(path, cwd)
    except ValueError as e:
        return {"type": "mkdir_result", "request_id": msg["request_id"], "success": False, "error": str(e)}

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
        return {"type": "mkdir_result", "request_id": msg["request_id"], **(await asyncio.to_thread(_mkdir))}
    except PermissionError:
        return {"type": "mkdir_result", "request_id": msg["request_id"], "success": False, "error": f"Permission denied: {resolved}"}
    except Exception as e:
        return {"type": "mkdir_result", "request_id": msg["request_id"], "success": False, "error": str(e)}


async def handle_delete(msg: dict) -> dict:
    """Delete a file or directory (recursive opt-in for directories)."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    recursive = bool(msg.get("recursive", False))

    try:
        resolved = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"type": "delete_result", "request_id": msg["request_id"], "success": False, "error": str(e)}

    # Refuse to delete the workspace root itself.
    base = os.path.realpath(cwd) if cwd else None
    if base and resolved == base:
        return {"type": "delete_result", "request_id": msg["request_id"], "success": False,
                "error": "Refusing to delete workspace root"}

    def _delete():
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
        return {"type": "delete_result", "request_id": msg["request_id"], **(await asyncio.to_thread(_delete))}
    except PermissionError:
        return {"type": "delete_result", "request_id": msg["request_id"], "success": False, "error": f"Permission denied: {resolved}"}
    except Exception as e:
        return {"type": "delete_result", "request_id": msg["request_id"], "success": False, "error": str(e)}


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
        return {"type": "move_result", "request_id": msg["request_id"], "success": False, "error": str(e)}

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
        return {"type": "move_result", "request_id": msg["request_id"], **(await asyncio.to_thread(_move))}
    except PermissionError as e:
        return {"type": "move_result", "request_id": msg["request_id"], "success": False, "error": f"Permission denied: {e}"}
    except Exception as e:
        return {"type": "move_result", "request_id": msg["request_id"], "success": False, "error": str(e)}


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
        return {"type": "copy_result", "request_id": msg["request_id"], "success": False, "error": str(e)}

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
        return {"type": "copy_result", "request_id": msg["request_id"], **(await asyncio.to_thread(_copy))}
    except PermissionError as e:
        return {"type": "copy_result", "request_id": msg["request_id"], "success": False, "error": f"Permission denied: {e}"}
    except Exception as e:
        return {"type": "copy_result", "request_id": msg["request_id"], "success": False, "error": str(e)}


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
        return {"type": "glob_result", "request_id": msg["request_id"], "error": "pattern is required"}

    try:
        base_dir = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"type": "glob_result", "request_id": msg["request_id"], "error": str(e)}

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
        return {"type": "glob_result", "request_id": msg["request_id"], **result}
    except Exception as e:
        return {"type": "glob_result", "request_id": msg["request_id"], "error": str(e)}


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
        logger.error("[grep] failed to launch ripgrep at %s: %s", RG_PATH, e)
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
        if etype == "match":
            if len(matches) >= max_results:
                truncated = True
                continue
            file_path = _decode_rg_text_field(data.get("path"))
            line_no = data.get("line_number")
            text = _decode_rg_text_field(data.get("lines")).rstrip("\n")[:500]
            matches.append({
                "file": file_path,
                "line": line_no,
                "text": text,
            })
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
            "type": "grep_result",
            "request_id": request_id,
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
    logger.info(
        "[grep] msg: pattern=%r path=%r cwd=%r glob=%r ci=%s gitignore=%s",
        pattern, path, cwd, glob_filter, case_insensitive, respect_gitignore,
    )
    try:
        max_results_raw = int(msg.get("max_results", MAX_GREP_RESULTS_DEFAULT))
    except (TypeError, ValueError):
        return {"type": "grep_result", "request_id": request_id,
                "error": "max_results must be an integer"}
    max_results = max(1, min(max_results_raw, 2000))

    if not pattern:
        return {"type": "grep_result", "request_id": request_id,
                "error": "pattern is required"}

    try:
        base_dir = _resolve_safe_path(path, cwd)
    except ValueError as e:
        logger.error("[grep] path validation failed: %s", e)
        return {"type": "grep_result", "request_id": request_id, "error": str(e)}
    logger.info("[grep] resolved base_dir=%s", base_dir)

    if not os.path.exists(base_dir):
        logger.error("[grep] base_dir does not exist: %s", base_dir)
        return {"type": "grep_result", "request_id": request_id,
                "error": f"Not a directory: {base_dir}"}

    try:
        result = await _grep_with_rg(
            base_dir=base_dir,
            pattern=pattern,
            glob_filter=glob_filter,
            case_insensitive=case_insensitive,
            max_results=max_results,
            respect_gitignore=respect_gitignore,
        )
    except RipgrepError as e:
        # Known failure modes (launch failure / timeout / non-zero exit)
        # surface as a structured error to the MCP layer.
        logger.error("[grep] RipgrepError: %s", e)
        return {"type": "grep_result", "request_id": request_id, "error": str(e)}
    # Any other exception (JSONDecodeError, programmer bugs, …) intentionally
    # propagates. The agent's outer dispatcher will log a stack trace and
    # the operator will see exactly what went wrong instead of a vague
    # "ripgrep failed" message.

    logger.info("[grep] returning %d matches for request_id=%s", result.get("count", 0), request_id)
    return {"type": "grep_result", "request_id": request_id, **result}


_HANDLERS = {
    "exec": handle_exec,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "list_dir": handle_list_dir,
    "stat": handle_stat,
    "mkdir": handle_mkdir,
    "delete": handle_delete,
    "move": handle_move,
    "copy": handle_copy,
    "glob": handle_glob,
    "grep": handle_grep,
}


# ── ChatManager ─────────────────────────────────────────────


class ChatManager:
    """Manages Claude Code chat sessions via stream-json CLI."""

    def __init__(self) -> None:
        # session_id → (process, request_id, task)
        self._active: dict[str, tuple[asyncio.subprocess.Process, str, asyncio.Task]] = {}

    def _find_claude(self) -> str:
        """Find the claude CLI executable."""
        claude = shutil.which("claude")
        if claude:
            return claude
        candidates = [
            Path.home() / ".claude" / "local" / "claude",
            Path.home() / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
        ]
        if IS_WINDOWS:
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
                Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
            ]
        for p in candidates:
            if p.exists():
                return str(p)
        return "claude"

    def _build_command(self, content: str, claude_session_id: str | None = None, model: str = "") -> list[str]:
        cmd = [self._find_claude(), "-p", content, "--output-format", "stream-json"]
        if claude_session_id:
            cmd.extend(["--resume", claude_session_id])
        if model:
            cmd.extend(["--model", model])
        return cmd

    async def handle_chat_message(self, msg: dict, send_fn) -> None:
        """Spawn claude CLI and stream events back via send_fn."""
        request_id = msg.get("request_id", "")
        session_id = msg.get("session_id", "")
        content = msg.get("content", "")
        claude_session_id = msg.get("claude_session_id")
        working_dir = msg.get("working_dir", "")
        model = msg.get("model", "")

        cmd = self._build_command(content, claude_session_id, model)
        cwd = working_dir if working_dir and os.path.isdir(working_dir) else None

        logger.info("Chat start: session=%s, cwd=%s, resume=%s", session_id, cwd, claude_session_id)

        proc = None
        try:
            kwargs = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": cwd,
            }
            if not IS_WINDOWS:
                kwargs["preexec_fn"] = os.setsid

            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
            # Track with current task for cancellation
            current_task = asyncio.current_task()
            self._active[session_id] = (proc, request_id, current_task)

            new_session_id = None
            cost_usd = None
            duration_ms = None

            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=CHAT_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning("Chat timeout: session=%s after %ds", session_id, CHAT_TIMEOUT)
                    _kill_process(proc)
                    await send_fn(json.dumps({
                        "type": "chat_error",
                        "request_id": request_id,
                        "session_id": session_id,
                        "error": f"Chat timed out after {CHAT_TIMEOUT // 60} minutes",
                    }))
                    return

                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "result":
                    new_session_id = event.get("session_id", new_session_id)
                    cost_usd = event.get("cost_usd", cost_usd)
                    duration_ms = event.get("duration_ms", duration_ms)
                    if event.get("subtype") == "error":
                        await send_fn(json.dumps({
                            "type": "chat_error",
                            "request_id": request_id,
                            "session_id": session_id,
                            "error": event.get("error", "Claude returned an error"),
                        }))
                        return

                await send_fn(json.dumps({
                    "type": "chat_event",
                    "request_id": request_id,
                    "session_id": session_id,
                    "event": event,
                }))

            await proc.wait()

            if proc.returncode == 0:
                await send_fn(json.dumps({
                    "type": "chat_complete",
                    "request_id": request_id,
                    "session_id": session_id,
                    "claude_session_id": new_session_id,
                    "cost_usd": cost_usd,
                    "duration_ms": duration_ms,
                }))
                logger.info("Chat complete: session=%s, claude=%s", session_id, new_session_id)
            else:
                stderr_bytes = await proc.stderr.read()
                error = stderr_bytes.decode("utf-8", errors="replace").strip()
                await send_fn(json.dumps({
                    "type": "chat_error",
                    "request_id": request_id,
                    "session_id": session_id,
                    "error": error or f"claude exited with code {proc.returncode}",
                }))
                logger.error("Chat error: session=%s, code=%d", session_id, proc.returncode)

        except asyncio.CancelledError:
            logger.info("Chat cancelled: session=%s", session_id)
            if proc and proc.returncode is None:
                _kill_process(proc)
            raise
        except Exception as e:
            logger.error("Chat exception: session=%s, error=%s", session_id, e)
            try:
                await send_fn(json.dumps({
                    "type": "chat_error",
                    "request_id": request_id,
                    "session_id": session_id,
                    "error": str(e),
                }))
            except Exception:
                pass
        finally:
            self._active.pop(session_id, None)
            # Ensure process is dead
            if proc and proc.returncode is None:
                _kill_process(proc)

    async def handle_cancel(self, msg: dict) -> None:
        """Cancel an active chat session."""
        session_id = msg.get("session_id", "")
        entry = self._active.get(session_id)
        if not entry:
            logger.warning("Cancel: no active session %s", session_id)
            return

        proc, request_id, task = entry
        logger.info("Cancelling chat: session=%s, pid=%s", session_id, proc.pid)
        _kill_process(proc)
        task.cancel()

    def cancel_all(self) -> None:
        """Cancel all active chat sessions (called on disconnect)."""
        for session_id, (proc, _, task) in list(self._active.items()):
            logger.info("Cleanup: killing chat session=%s, pid=%s", session_id, proc.pid)
            _kill_process(proc)
            task.cancel()
        self._active.clear()

    def get_active_sessions(self) -> list[str]:
        return list(self._active.keys())


# ── Agent ────────────────────────────────────────────────────


class TerminalAgent:
    def __init__(self, server_url: str, token: str):
        self.server_url = server_url
        self.token = token
        self._ws = None
        self._running = True
        self._agent_id: str | None = None
        self._chat_manager = ChatManager()
        self._send_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

    async def _safe_send(self, data: str) -> None:
        """Send data on WebSocket with lock to prevent frame interleaving."""
        ws = self._ws
        if not ws:
            return
        async with self._send_lock:
            try:
                await ws.send(data)
            except Exception as e:
                logger.warning("Send failed: %s", e)

    def _spawn_task(self, coro) -> None:
        """Spawn a background task and track it for cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def run(self) -> None:
        backoff = 1
        max_backoff = 60

        while self._running:
            try:
                await self._connect()
                backoff = 1
            except Exception as e:
                logger.error("Connection failed: %s", e)

            if not self._running:
                break

            logger.info("Reconnecting in %ds...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _connect(self) -> None:
        import websockets

        logger.info("Connecting to %s", self.server_url)

        async with websockets.connect(
            self.server_url, ping_interval=20, ping_timeout=10,
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
                "os": sys.platform,
                "shells": _detect_shells(),
            }))

            # ── Message loop ──
            try:
                async for raw in ws:
                    if not self._running:
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_message(msg)
            finally:
                # Cleanup on disconnect: kill all chat processes
                self._ws = None
                self._chat_manager.cancel_all()
                # Cancel background tasks
                for task in list(self._background_tasks):
                    task.cancel()
                self._background_tasks.clear()

    async def _handle_message(self, msg: dict) -> None:
        msg_type = msg.get("type")

        # Chat messages: fire-and-forget (long-running, streaming)
        if msg_type == "chat_message":
            self._spawn_task(
                self._chat_manager.handle_chat_message(msg, self._safe_send)
            )
            return

        if msg_type == "chat_cancel":
            await self._chat_manager.handle_cancel(msg)
            return

        # Regular handlers: also run as tasks to avoid blocking the loop
        handler = _HANDLERS.get(msg_type)
        if handler:
            self._spawn_task(self._run_handler(handler, msg))
            return

        if msg_type == "ping":
            await self._safe_send(json.dumps({"type": "pong"}))

    async def _run_handler(self, handler, msg: dict) -> None:
        """Run a request/response handler as a background task."""
        msg_type = msg.get("type")
        request_id = msg.get("request_id")
        try:
            response = await handler(msg)
            if response:
                try:
                    payload = json.dumps(response)
                except (TypeError, ValueError) as e:
                    logger.exception(
                        "[dispatch] FAILED to serialize response for %s/%s: %s",
                        msg_type, request_id, e,
                    )
                    # Send a minimal error so the caller doesn't hang.
                    payload = json.dumps({
                        "type": f"{msg_type}_result",
                        "request_id": request_id,
                        "error": f"response serialization failed: {e}",
                    })
                logger.info(
                    "[dispatch] sending response: type=%s req=%s bytes=%d",
                    response.get("type"), request_id, len(payload),
                )
                await self._safe_send(payload)
                logger.info(
                    "[dispatch] response sent: type=%s req=%s",
                    response.get("type"), request_id,
                )
        except Exception as e:
            # Full stack trace so operators can see WHERE the handler died,
            # not just the exception message.
            logger.exception("[dispatch] Handler error for %s/%s: %s",
                             msg_type, request_id, e)
            if request_id:
                await self._safe_send(json.dumps({
                    "type": f"{msg_type or 'unknown'}_result",
                    "request_id": request_id,
                    "error": f"{type(e).__name__}: {e}",
                }))

    async def shutdown(self) -> None:
        self._running = False
        self._chat_manager.cancel_all()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass


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

    server_url = args.url
    token = args.token

    if args.config:
        cfg = load_config(args.config)
        server_url = server_url or cfg.get("server_url", "")
        token = token or cfg.get("token", "")

    if not server_url or not token:
        parser.error("--url and --token are required (or provide --config)")

    agent = TerminalAgent(server_url, token)

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
