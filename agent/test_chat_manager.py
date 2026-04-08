"""Unit tests for Agent ChatManager."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from main import ChatManager, TerminalAgent, CHAT_TIMEOUT


@pytest.fixture
def chat_manager():
    return ChatManager()


@pytest.fixture
def send_fn():
    """Mock async send function that captures sent messages."""
    messages = []

    async def _send(data: str):
        messages.append(json.loads(data))

    _send.messages = messages
    return _send


class TestBuildCommand:
    def test_basic_command(self, chat_manager):
        with patch.object(chat_manager, "_find_claude", return_value="claude"):
            cmd = chat_manager._build_command("hello")
        assert cmd == ["claude", "-p", "hello", "--output-format", "stream-json"]

    def test_with_resume(self, chat_manager):
        with patch.object(chat_manager, "_find_claude", return_value="claude"):
            cmd = chat_manager._build_command("hello", claude_session_id="abc123")
        assert "--resume" in cmd
        assert "abc123" in cmd

    def test_with_model(self, chat_manager):
        with patch.object(chat_manager, "_find_claude", return_value="claude"):
            cmd = chat_manager._build_command("hello", model="sonnet")
        assert "--model" in cmd
        assert "sonnet" in cmd

    def test_with_all_options(self, chat_manager):
        with patch.object(chat_manager, "_find_claude", return_value="claude"):
            cmd = chat_manager._build_command("fix bug", claude_session_id="s1", model="opus")
        assert cmd == [
            "claude", "-p", "fix bug", "--output-format", "stream-json",
            "--resume", "s1", "--model", "opus",
        ]


class TestHandleChatMessage:
    @pytest.mark.asyncio
    async def test_successful_chat(self, chat_manager, send_fn):
        stream_events = [
            json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
            json.dumps({"type": "result", "session_id": "claude-new-123", "cost_usd": 0.01, "duration_ms": 500}),
        ]

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = AsyncMock()
        lines = [line.encode() + b"\n" for line in stream_events] + [b""]
        mock_proc.stdout.readline = AsyncMock(side_effect=lines)
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock(return_value=0)

        msg = {
            "request_id": "req1", "session_id": "sess1",
            "content": "hello", "working_dir": "",
        }

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch.object(chat_manager, "_find_claude", return_value="claude"), \
             patch("asyncio.current_task", return_value=MagicMock()):
            await chat_manager.handle_chat_message(msg, send_fn)

        assert len(send_fn.messages) == 3
        assert send_fn.messages[0]["type"] == "chat_event"
        assert send_fn.messages[2]["type"] == "chat_complete"
        assert send_fn.messages[2]["claude_session_id"] == "claude-new-123"
        assert "sess1" not in chat_manager._active

    @pytest.mark.asyncio
    async def test_claude_nonzero_exit(self, chat_manager, send_fn):
        mock_proc = AsyncMock()
        mock_proc.pid = 99
        mock_proc.returncode = 1
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"ANTHROPIC_API_KEY not set")
        mock_proc.wait = AsyncMock(return_value=1)

        msg = {"request_id": "req2", "session_id": "sess2", "content": "test", "working_dir": ""}

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch.object(chat_manager, "_find_claude", return_value="claude"), \
             patch("asyncio.current_task", return_value=MagicMock()):
            await chat_manager.handle_chat_message(msg, send_fn)

        assert send_fn.messages[-1]["type"] == "chat_error"
        assert "ANTHROPIC_API_KEY" in send_fn.messages[-1]["error"]

    @pytest.mark.asyncio
    async def test_subprocess_exception(self, chat_manager, send_fn):
        msg = {"request_id": "req3", "session_id": "sess3", "content": "test", "working_dir": ""}

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude not found")), \
             patch.object(chat_manager, "_find_claude", return_value="claude"):
            await chat_manager.handle_chat_message(msg, send_fn)

        assert send_fn.messages[-1]["type"] == "chat_error"
        assert "not found" in send_fn.messages[-1]["error"]

    @pytest.mark.asyncio
    async def test_timeout(self, chat_manager, send_fn):
        """Chat timeout kills the process and sends error."""
        mock_proc = AsyncMock()
        mock_proc.pid = 42
        mock_proc.returncode = None
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = MagicMock()

        msg = {"request_id": "req4", "session_id": "sess4", "content": "test", "working_dir": ""}

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch.object(chat_manager, "_find_claude", return_value="claude"), \
             patch("asyncio.current_task", return_value=MagicMock()):
            await chat_manager.handle_chat_message(msg, send_fn)

        assert send_fn.messages[-1]["type"] == "chat_error"
        assert "timed out" in send_fn.messages[-1]["error"].lower()


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancel_all_kills_processes(self, chat_manager):
        proc1 = MagicMock()
        proc1.pid = 100
        proc1.returncode = None
        proc1.kill = MagicMock()
        task1 = MagicMock()

        proc2 = MagicMock()
        proc2.pid = 200
        proc2.returncode = None
        proc2.kill = MagicMock()
        task2 = MagicMock()

        chat_manager._active = {
            "s1": (proc1, "r1", task1),
            "s2": (proc2, "r2", task2),
        }

        chat_manager.cancel_all()

        proc1.kill.assert_called()
        proc2.kill.assert_called()
        task1.cancel.assert_called_once()
        task2.cancel.assert_called_once()
        assert len(chat_manager._active) == 0


class TestHandleCancel:
    @pytest.mark.asyncio
    async def test_cancel_active_session(self, chat_manager):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()
        mock_task = MagicMock()

        chat_manager._active["sess5"] = (mock_proc, "req5", mock_task)

        await chat_manager.handle_cancel({"session_id": "sess5"})
        mock_proc.kill.assert_called()
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_session(self, chat_manager):
        await chat_manager.handle_cancel({"session_id": "nonexistent"})


class TestSendLock:
    @pytest.mark.asyncio
    async def test_safe_send_serializes(self):
        """Verify sends are serialized via lock."""
        agent = TerminalAgent("ws://test", "token")
        order = []

        class FakeWs:
            async def send(self, data):
                order.append("start")
                await asyncio.sleep(0.01)
                order.append("end")

        agent._ws = FakeWs()

        await asyncio.gather(
            agent._safe_send("msg1"),
            agent._safe_send("msg2"),
        )

        # With lock: start-end-start-end (no interleaving)
        assert order == ["start", "end", "start", "end"]

    @pytest.mark.asyncio
    async def test_safe_send_handles_none_ws(self):
        agent = TerminalAgent("ws://test", "token")
        agent._ws = None
        await agent._safe_send("msg")  # Should not raise


class TestSpawnTask:
    @pytest.mark.asyncio
    async def test_task_tracked_and_cleaned(self):
        agent = TerminalAgent("ws://test", "token")

        async def quick():
            await asyncio.sleep(0.01)

        agent._spawn_task(quick())
        assert len(agent._background_tasks) == 1

        await asyncio.sleep(0.05)
        assert len(agent._background_tasks) == 0


# ──────────────────────────────────────────────
# TerminalAgent._absolute_download_url
# ──────────────────────────────────────────────


class TestAbsoluteDownloadUrl:
    """Regression: _absolute_download_url must swap scheme wss→https and
    cope with server-relative paths that may or may not include the
    leading slash.
    """

    def _agent(self, server_url: str) -> TerminalAgent:
        return TerminalAgent(server_url=server_url, token="ta_x")

    def test_wss_to_https_with_leading_slash(self):
        agent = self._agent("wss://todo.example.com/api/v1/terminal/agent/ws")
        url = agent._absolute_download_url("/api/v1/terminal/releases/abc/download")
        assert url == "https://todo.example.com/api/v1/terminal/releases/abc/download"

    def test_ws_to_http(self):
        agent = self._agent("ws://localhost:8000/api/v1/terminal/agent/ws")
        url = agent._absolute_download_url("/api/v1/terminal/releases/abc/download")
        assert url == "http://localhost:8000/api/v1/terminal/releases/abc/download"

    def test_missing_leading_slash_normalized(self):
        agent = self._agent("wss://todo.example.com/api/v1/terminal/agent/ws")
        url = agent._absolute_download_url("api/v1/terminal/releases/abc/download")
        assert url == "https://todo.example.com/api/v1/terminal/releases/abc/download"

    def test_port_preserved(self):
        agent = self._agent("wss://todo.example.com:8443/api/v1/terminal/agent/ws")
        url = agent._absolute_download_url("/api/v1/terminal/releases/abc/download")
        assert url == "https://todo.example.com:8443/api/v1/terminal/releases/abc/download"

