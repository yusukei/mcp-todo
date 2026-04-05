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
            if IS_WINDOWS:
                proc.kill()
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    proc.kill()
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

    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)

    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return {
                "type": "file_content",
                "request_id": msg["request_id"],
                "error": f"File too large: {size} bytes (max {MAX_FILE_BYTES // 1024 // 1024} MB)",
            }

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        return {
            "type": "file_content",
            "request_id": msg["request_id"],
            "content": content,
            "size": size,
            "path": path,
        }
    except FileNotFoundError:
        return {
            "type": "file_content",
            "request_id": msg["request_id"],
            "error": f"File not found: {path}",
        }
    except PermissionError:
        return {
            "type": "file_content",
            "request_id": msg["request_id"],
            "error": f"Permission denied: {path}",
        }
    except Exception as e:
        return {
            "type": "file_content",
            "request_id": msg["request_id"],
            "error": str(e),
        }


async def handle_write_file(msg: dict) -> dict:
    """Write content to a file."""
    path = msg.get("path", "")
    cwd = msg.get("cwd")
    content = msg.get("content", "")

    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)

    try:
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            return {
                "type": "write_result",
                "request_id": msg["request_id"],
                "success": False,
                "error": f"Content too large: {len(data)} bytes (max {MAX_FILE_BYTES // 1024 // 1024} MB)",
            }

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return {
            "type": "write_result",
            "request_id": msg["request_id"],
            "success": True,
            "bytes_written": len(data),
            "path": path,
        }
    except PermissionError:
        return {
            "type": "write_result",
            "request_id": msg["request_id"],
            "success": False,
            "error": f"Permission denied: {path}",
        }
    except Exception as e:
        return {
            "type": "write_result",
            "request_id": msg["request_id"],
            "success": False,
            "error": str(e),
        }


async def handle_list_dir(msg: dict) -> dict:
    """List directory contents."""
    path = msg.get("path", ".")
    cwd = msg.get("cwd")

    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)

    try:
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
                        "modified": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    })
                except (OSError, PermissionError):
                    entries.append({
                        "name": entry.name,
                        "type": "unknown",
                        "size": 0,
                        "modified": "",
                    })

        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))

        return {
            "type": "dir_listing",
            "request_id": msg["request_id"],
            "entries": entries,
            "path": path,
        }
    except FileNotFoundError:
        return {
            "type": "dir_listing",
            "request_id": msg["request_id"],
            "error": f"Directory not found: {path}",
        }
    except PermissionError:
        return {
            "type": "dir_listing",
            "request_id": msg["request_id"],
            "error": f"Permission denied: {path}",
        }
    except Exception as e:
        return {
            "type": "dir_listing",
            "request_id": msg["request_id"],
            "error": str(e),
        }


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
        # session_id → (process, request_id)
        self._active: dict[str, tuple[asyncio.subprocess.Process, str]] = {}

    def _find_claude(self) -> str:
        """Find the claude CLI executable."""
        claude = shutil.which("claude")
        if claude:
            return claude
        # Common install paths
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
        return "claude"  # fallback to PATH

    def _build_command(
        self,
        content: str,
        claude_session_id: str | None = None,
        model: str = "",
    ) -> list[str]:
        """Build claude CLI command."""
        cmd = [self._find_claude(), "-p", content, "--output-format", "stream-json"]
        if claude_session_id:
            cmd.extend(["--resume", claude_session_id])
        if model:
            cmd.extend(["--model", model])
        return cmd

    async def handle_chat_message(self, msg: dict, send_fn) -> None:
        """Spawn claude CLI and stream events back via send_fn.

        send_fn: async callable that sends a JSON string to the WebSocket.
        """
        request_id = msg.get("request_id", "")
        session_id = msg.get("session_id", "")
        content = msg.get("content", "")
        claude_session_id = msg.get("claude_session_id")
        working_dir = msg.get("working_dir", "")
        model = msg.get("model", "")

        cmd = self._build_command(content, claude_session_id, model)
        cwd = working_dir if working_dir and os.path.isdir(working_dir) else None

        logger.info("Chat start: session=%s, cwd=%s, resume=%s", session_id, cwd, claude_session_id)

        try:
            kwargs = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "cwd": cwd,
            }
            if not IS_WINDOWS:
                kwargs["preexec_fn"] = os.setsid

            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
            self._active[session_id] = (proc, request_id)

            # Stream stdout line by line
            new_session_id = None
            cost_usd = None
            duration_ms = None

            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue

                # Extract metadata from result event
                if event.get("type") == "result":
                    new_session_id = event.get("session_id", new_session_id)
                    cost_usd = event.get("cost_usd", cost_usd)
                    duration_ms = event.get("duration_ms", duration_ms)
                    # Also check subtype for error
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

    async def handle_cancel(self, msg: dict) -> None:
        """Cancel an active chat session by killing the claude process."""
        session_id = msg.get("session_id", "")
        entry = self._active.get(session_id)
        if not entry:
            logger.warning("Cancel: no active session %s", session_id)
            return

        proc, request_id = entry
        logger.info("Cancelling chat: session=%s, pid=%s", session_id, proc.pid)
        try:
            if IS_WINDOWS:
                proc.kill()
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                except (OSError, ProcessLookupError):
                    proc.kill()
        except ProcessLookupError:
            pass

    def get_active_sessions(self) -> list[str]:
        """Return list of active session IDs."""
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

            # Send agent info
            await ws.send(json.dumps({
                "type": "agent_info",
                "hostname": platform.node(),
                "os": sys.platform,
                "shells": _detect_shells(),
            }))

            # ── Message loop ──
            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_message(msg)

    async def _handle_message(self, msg: dict) -> None:
        msg_type = msg.get("type")

        # Chat messages: fire-and-forget (long-running, streaming)
        if msg_type == "chat_message":
            async def _send(data: str):
                if self._ws:
                    await self._ws.send(data)
            asyncio.create_task(
                self._chat_manager.handle_chat_message(msg, _send)
            )
            return

        if msg_type == "chat_cancel":
            await self._chat_manager.handle_cancel(msg)
            return

        # Regular request/response handlers
        handler = _HANDLERS.get(msg_type)
        if handler:
            response = await handler(msg)
            if self._ws and response:
                try:
                    await self._ws.send(json.dumps(response))
                except Exception as e:
                    logger.error("Failed to send response: %s", e)
            return

        if msg_type == "ping":
            if self._ws:
                await self._ws.send(json.dumps({"type": "pong"}))

    async def shutdown(self) -> None:
        self._running = False
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
