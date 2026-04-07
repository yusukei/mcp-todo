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
import json
import logging
import os
import platform
import shutil
import signal
import sys
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
CHAT_TIMEOUT = 30 * 60  # 30 minutes max per chat message


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


# ── Handlers ────────────────────────────────────────────────


async def handle_exec(msg: dict) -> dict:
    """Execute a shell command and return stdout/stderr."""
    command = msg.get("command", "")
    cwd = msg.get("cwd")
    timeout = min(msg.get("timeout", 60), 300)

    if cwd and not os.path.isdir(cwd):
        return {
            "type": "exec_result",
            "request_id": msg["request_id"],
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Working directory does not exist: {cwd}",
        }

    try:
        kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": cwd,
        }
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
            return {
                "type": "exec_result",
                "request_id": msg["request_id"],
                "exit_code": -1,
                "stdout": stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
                "stderr": stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
                + f"\n[timeout after {timeout}s]",
            }

        return {
            "type": "exec_result",
            "request_id": msg["request_id"],
            "exit_code": proc.returncode,
            "stdout": stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
            "stderr": stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
        }

    except Exception as e:
        return {
            "type": "exec_result",
            "request_id": msg["request_id"],
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
        }


async def handle_read_file(msg: dict) -> dict:
    """Read a file and return its content."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")

    try:
        path = _resolve_safe_path(path, cwd)
    except ValueError as e:
        return {"type": "file_content", "request_id": msg["request_id"], "error": str(e)}

    def _read():
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return {"error": f"File too large: {size} bytes (max {MAX_FILE_BYTES // 1024 // 1024} MB)"}
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return {"content": f.read(), "size": size, "path": path}

    try:
        result = await asyncio.to_thread(_read)
        return {"type": "file_content", "request_id": msg["request_id"], **result}
    except FileNotFoundError:
        return {"type": "file_content", "request_id": msg["request_id"], "error": f"File not found: {path}"}
    except PermissionError:
        return {"type": "file_content", "request_id": msg["request_id"], "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"type": "file_content", "request_id": msg["request_id"], "error": str(e)}


async def handle_write_file(msg: dict) -> dict:
    """Write content to a file."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    content = msg.get("content", "")

    try:
        path = _resolve_safe_path(path, cwd)
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


_HANDLERS = {
    "exec": handle_exec,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "list_dir": handle_list_dir,
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
        try:
            response = await handler(msg)
            if response:
                await self._safe_send(json.dumps(response))
        except Exception as e:
            logger.error("Handler error for %s: %s", msg.get("type"), e)
            # Send error response so backend Future doesn't hang
            request_id = msg.get("request_id")
            if request_id:
                await self._safe_send(json.dumps({
                    "type": f"{msg.get('type', 'unknown')}_result",
                    "request_id": request_id,
                    "error": str(e),
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


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Todo Remote Terminal Agent")
    parser.add_argument("--url", help="WebSocket server URL")
    parser.add_argument("--token", help="Agent authentication token")
    parser.add_argument("--config", help="Path to config JSON file", default="")
    args = parser.parse_args()

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
