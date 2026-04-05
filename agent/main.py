#!/usr/bin/env python3
"""MCP Todo — Remote Terminal Agent.

Connects to the central server via WebSocket and relays PTY I/O.
Supports macOS/Linux (stdlib pty) and Windows (pywinpty).

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
import signal
import struct
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("terminal-agent")

# ── Platform detection ───────────────────────────────────────

IS_WINDOWS = sys.platform == "win32"


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


def _default_shell() -> str:
    if IS_WINDOWS:
        return os.environ.get("COMSPEC", r"C:\Windows\system32\cmd.exe")
    return os.environ.get("SHELL", "/bin/sh")


# ── PTY Backend ──────────────────────────────────────────────

class PtySession:
    """Cross-platform PTY session."""

    def __init__(self):
        self._process = None
        self._fd: int | None = None
        self._pid: int | None = None
        self._winpty = None  # Windows only
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
        import termios

        pid, fd = pty.openpty()
        # Set window size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        child_pid = os.fork()
        if child_pid == 0:
            # Child process
            os.close(pid)
            os.setsid()
            import tty
            tty.setraw(fd)

            # Set fd as controlling terminal
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
        from winpty import PtyProcess  # pywinpty package

        self._winpty = PtyProcess.spawn(
            shell,
            dimensions=(rows, cols),
        )

    async def read(self) -> bytes | None:
        if not self._alive:
            return None

        if IS_WINDOWS:
            return await self._read_windows()
        else:
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
        """Blocking read — run in executor."""
        try:
            data = self._winpty.read(4096)  # type: ignore[union-attr]
            if not data:
                return None
            return data.encode("utf-8", errors="replace") if isinstance(data, str) else data
        except EOFError:
            return None
        except Exception:
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
                await loop.run_in_executor(
                    None, os.write, self._fd, data.encode() if isinstance(data, str) else data
                )

    async def resize(self, cols: int, rows: int) -> None:
        if IS_WINDOWS:
            if self._winpty:
                try:
                    self._winpty.setwinsize(rows, cols)
                except Exception:
                    pass
        else:
            if self._fd is not None:
                import fcntl
                import termios
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                try:
                    fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
                except OSError:
                    pass
            # Send SIGWINCH to child
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
                    pass
                self._winpty = None
        else:
            if self._pid:
                try:
                    os.kill(self._pid, signal.SIGTERM)
                    _, status = os.waitpid(self._pid, 0)
                    exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
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
                    return self._winpty.isalive()
                except Exception:
                    return False
            return False
        else:
            if self._pid:
                try:
                    pid, _ = os.waitpid(self._pid, os.WNOHANG)
                    return pid == 0
                except ChildProcessError:
                    return False
            return False


# ── Agent ────────────────────────────────────────────────────

class TerminalAgent:
    def __init__(self, server_url: str, token: str, default_shell: str = ""):
        self.server_url = server_url
        self.token = token
        self.default_shell = default_shell or _default_shell()
        self.pty_session: PtySession | None = None
        self._ws = None
        self._running = True
        self._read_task: asyncio.Task | None = None

    async def run(self) -> None:
        """Main loop with exponential backoff reconnection."""
        backoff = 1
        max_backoff = 60

        while self._running:
            try:
                await self._connect()
                backoff = 1  # Reset on successful connection
            except Exception as e:
                logger.error("Connection failed: %s", e)

            if not self._running:
                break

            logger.info("Reconnecting in %ds...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _connect(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required. Install with: pip install websockets")
            self._running = False
            return

        url = f"{self.server_url}?token={self.token}"
        logger.info("Connecting to %s", self.server_url)

        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            logger.info("Connected to server")

            # Send agent info
            await ws.send(json.dumps({
                "type": "agent_info",
                "hostname": platform.node(),
                "os": sys.platform,
                "shells": _detect_shells(),
            }))

            # Message loop
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

        if msg_type == "session_start":
            await self._start_session(
                shell=msg.get("shell") or self.default_shell,
                cols=msg.get("cols", 120),
                rows=msg.get("rows", 40),
            )

        elif msg_type == "input":
            if self.pty_session and self.pty_session.alive:
                await self.pty_session.write(msg.get("data", ""))

        elif msg_type == "resize":
            if self.pty_session:
                await self.pty_session.resize(
                    cols=msg.get("cols", 120),
                    rows=msg.get("rows", 40),
                )

        elif msg_type == "session_end":
            await self._end_session()

        elif msg_type == "ping":
            if self._ws:
                await self._ws.send(json.dumps({"type": "pong"}))

    async def _start_session(self, shell: str, cols: int, rows: int) -> None:
        # End any existing session
        await self._end_session()

        logger.info("Starting PTY: shell=%s cols=%d rows=%d", shell, cols, rows)
        self.pty_session = PtySession()
        try:
            await self.pty_session.spawn(shell, cols, rows)
        except Exception as e:
            logger.error("Failed to spawn PTY: %s", e)
            if self._ws:
                await self._ws.send(json.dumps({
                    "type": "exited",
                    "exit_code": -1,
                }))
            self.pty_session = None
            return

        # Start reading PTY output in background
        self._read_task = asyncio.create_task(self._pty_reader())

    async def _pty_reader(self) -> None:
        """Read PTY output and forward to server."""
        try:
            while self.pty_session and self.pty_session.alive:
                data = await self.pty_session.read()
                if data is None:
                    break
                if self._ws:
                    # Decode with replace to handle binary/encoding issues
                    text = data.decode("utf-8", errors="replace")
                    await self._ws.send(json.dumps({
                        "type": "output",
                        "data": text,
                    }))
        except Exception as e:
            logger.debug("PTY reader ended: %s", e)
        finally:
            # PTY process ended
            exit_code = -1
            if self.pty_session:
                exit_code = await self.pty_session.terminate()
                self.pty_session = None
            if self._ws:
                try:
                    await self._ws.send(json.dumps({
                        "type": "exited",
                        "exit_code": exit_code,
                    }))
                except Exception:
                    pass

    async def _end_session(self) -> None:
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        if self.pty_session:
            await self.pty_session.terminate()
            self.pty_session = None

    async def shutdown(self) -> None:
        self._running = False
        await self._end_session()
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
    parser.add_argument("--shell", help="Default shell to use", default="")
    parser.add_argument("--config", help="Path to config JSON file", default="")
    args = parser.parse_args()

    server_url = args.url
    token = args.token
    default_shell = args.shell

    if args.config:
        cfg = load_config(args.config)
        server_url = server_url or cfg.get("server_url", "")
        token = token or cfg.get("token", "")
        default_shell = default_shell or cfg.get("default_shell", "")

    if not server_url or not token:
        parser.error("--url and --token are required (or provide --config)")

    agent = TerminalAgent(server_url, token, default_shell)

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
