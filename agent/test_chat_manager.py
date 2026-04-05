"""Unit tests for Agent ChatManager."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import from main.py in the same directory
sys.path.insert(0, os.path.dirname(__file__))
from main import ChatManager


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
        """Simulate claude producing stream-json events and completing."""
        stream_events = [
            json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
            json.dumps({"type": "result", "session_id": "claude-new-123", "cost_usd": 0.01, "duration_ms": 500}),
        ]
        stdout = ("\n".join(stream_events) + "\n").encode()

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = AsyncMock()

        # Simulate readline returning lines then empty
        lines = [line.encode() + b"\n" for line in stream_events] + [b""]
        mock_proc.stdout.readline = AsyncMock(side_effect=lines)
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock(return_value=0)

        msg = {
            "request_id": "req1",
            "session_id": "sess1",
            "content": "hello",
            "claude_session_id": None,
            "working_dir": "",
            "model": "",
        }

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch.object(chat_manager, "_find_claude", return_value="claude"):
            await chat_manager.handle_chat_message(msg, send_fn)

        # Should have sent: 2 chat_events + 1 chat_complete
        assert len(send_fn.messages) == 3

        # First two are events
        assert send_fn.messages[0]["type"] == "chat_event"
        assert send_fn.messages[1]["type"] == "chat_event"

        # Last is complete
        complete = send_fn.messages[2]
        assert complete["type"] == "chat_complete"
        assert complete["claude_session_id"] == "claude-new-123"
        assert complete["cost_usd"] == 0.01

        # Session should no longer be active
        assert "sess1" not in chat_manager._active

    @pytest.mark.asyncio
    async def test_claude_nonzero_exit(self, chat_manager, send_fn):
        """Claude exits with non-zero code → chat_error."""
        mock_proc = AsyncMock()
        mock_proc.pid = 99
        mock_proc.returncode = 1
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"ANTHROPIC_API_KEY not set")
        mock_proc.wait = AsyncMock(return_value=1)

        msg = {
            "request_id": "req2",
            "session_id": "sess2",
            "content": "test",
            "working_dir": "",
        }

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch.object(chat_manager, "_find_claude", return_value="claude"):
            await chat_manager.handle_chat_message(msg, send_fn)

        assert len(send_fn.messages) == 1
        assert send_fn.messages[0]["type"] == "chat_error"
        assert "ANTHROPIC_API_KEY" in send_fn.messages[0]["error"]

    @pytest.mark.asyncio
    async def test_subprocess_exception(self, chat_manager, send_fn):
        """Exception during subprocess spawn → chat_error."""
        msg = {
            "request_id": "req3",
            "session_id": "sess3",
            "content": "test",
            "working_dir": "",
        }

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude not found")), \
             patch.object(chat_manager, "_find_claude", return_value="claude"):
            await chat_manager.handle_chat_message(msg, send_fn)

        assert len(send_fn.messages) == 1
        assert send_fn.messages[0]["type"] == "chat_error"
        assert "not found" in send_fn.messages[0]["error"]

    @pytest.mark.asyncio
    async def test_active_session_tracking(self, chat_manager, send_fn):
        """Session is tracked while active, removed when done."""
        mock_proc = AsyncMock()
        mock_proc.pid = 42
        mock_proc.returncode = 0
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock(return_value=0)

        msg = {
            "request_id": "req4",
            "session_id": "sess4",
            "content": "test",
            "working_dir": "",
        }

        registered = []

        original = asyncio.create_subprocess_exec

        async def track_spawn(*args, **kwargs):
            registered.append(chat_manager.get_active_sessions())
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=track_spawn), \
             patch.object(chat_manager, "_find_claude", return_value="claude"):
            await chat_manager.handle_chat_message(msg, send_fn)

        # After completion, session should be removed
        assert "sess4" not in chat_manager._active


class TestHandleCancel:
    @pytest.mark.asyncio
    async def test_cancel_active_session(self, chat_manager):
        """Cancel kills the active process."""
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.kill = MagicMock()

        chat_manager._active["sess5"] = (mock_proc, "req5")

        await chat_manager.handle_cancel({"session_id": "sess5"})
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_session(self, chat_manager):
        """Cancel on non-active session is a no-op."""
        await chat_manager.handle_cancel({"session_id": "nonexistent"})
        # Should not raise
