"""Unit tests for AgentConnectionManager.

Targets the in-process state machine: register/unregister, request/response
correlation, timeout, and the offline-cancel guarantee that pending Futures
get failed when an agent disconnects.

These tests use a tiny FakeWebSocket so they don't require an HTTP server.
"""

import asyncio
import json
from typing import Any

import pytest

from app.services.agent_manager import (
    AgentConnectionManager,
    AgentOfflineError,
    CommandTimeoutError,
)


class FakeWebSocket:
    """Minimal WebSocket double — records every send_text payload."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_next = False

    async def send_text(self, payload: str) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("send failed")
        self.sent.append(payload)


@pytest.fixture
def manager() -> AgentConnectionManager:
    return AgentConnectionManager()


class TestRegisterUnregister:
    def test_register_returns_none_on_first_connection(self, manager):
        ws = FakeWebSocket()
        old = manager.register("agent-1", ws)
        assert old is None
        assert manager.is_connected("agent-1")
        assert "agent-1" in manager.get_connected_agent_ids()

    def test_register_replaces_existing_connection(self, manager):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        manager.register("agent-1", ws1)
        old = manager.register("agent-1", ws2)
        assert old is ws1  # caller is told the old conn so it can close it
        assert manager.is_connected("agent-1")

    def test_unregister_removes_when_ws_matches(self, manager):
        ws = FakeWebSocket()
        manager.register("agent-1", ws)
        manager.unregister("agent-1", ws)
        assert not manager.is_connected("agent-1")

    def test_unregister_with_stale_ws_is_noop(self, manager):
        """A stale handler must not delete a newer reconnection."""
        old_ws = FakeWebSocket()
        new_ws = FakeWebSocket()
        manager.register("agent-1", old_ws)
        manager.register("agent-1", new_ws)  # reconnect

        manager.unregister("agent-1", old_ws)  # late cleanup from old handler
        assert manager.is_connected("agent-1")  # newer connection still alive

    def test_unregister_without_ws_force_removes(self, manager):
        """delete_agent passes ws=None to unconditionally remove."""
        manager.register("agent-1", FakeWebSocket())
        manager.unregister("agent-1")
        assert not manager.is_connected("agent-1")


class TestSendRequest:
    async def test_send_request_offline_raises(self, manager):
        with pytest.raises(AgentOfflineError):
            await manager.send_request("missing", "exec", {})

    async def test_send_request_success(self, manager):
        ws = FakeWebSocket()
        manager.register("agent-1", ws)

        async def respond_after_delay():
            # Give send_request time to register the future, then resolve it
            await asyncio.sleep(0.01)
            payload = json.loads(ws.sent[0])
            request_id = payload["request_id"]
            manager.resolve_request({
                "request_id": request_id,
                "exit_code": 0,
                "stdout": "hello",
            })

        responder = asyncio.create_task(respond_after_delay())
        result = await manager.send_request(
            "agent-1", "exec", {"command": "echo hi"}, timeout=2.0
        )
        await responder

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello"

    async def test_send_request_runtime_error_on_error_payload(self, manager):
        ws = FakeWebSocket()
        manager.register("agent-1", ws)

        async def respond_with_error():
            await asyncio.sleep(0.01)
            request_id = json.loads(ws.sent[0])["request_id"]
            manager.resolve_request({
                "request_id": request_id,
                "error": "command not found",
            })

        asyncio.create_task(respond_with_error())
        with pytest.raises(RuntimeError, match="command not found"):
            await manager.send_request("agent-1", "exec", {"command": "boom"}, timeout=2.0)

    async def test_send_request_timeout(self, manager):
        ws = FakeWebSocket()
        manager.register("agent-1", ws)

        with pytest.raises(CommandTimeoutError):
            await manager.send_request("agent-1", "exec", {}, timeout=0.05)

    async def test_disconnect_cancels_pending_request(self, manager):
        """When an agent goes offline mid-request, the future must fail."""
        ws = FakeWebSocket()
        manager.register("agent-1", ws)

        async def disconnect_soon():
            await asyncio.sleep(0.01)
            manager.unregister("agent-1", ws)

        asyncio.create_task(disconnect_soon())
        with pytest.raises(AgentOfflineError):
            await manager.send_request("agent-1", "exec", {}, timeout=2.0)


class TestSendRaw:
    async def test_send_raw_writes_json(self, manager):
        ws = FakeWebSocket()
        manager.register("agent-1", ws)
        await manager.send_raw("agent-1", {"type": "ping"})
        assert ws.sent == ['{"type": "ping"}']

    async def test_send_raw_offline_raises(self, manager):
        with pytest.raises(AgentOfflineError):
            await manager.send_raw("missing", {"type": "ping"})


class TestResolveRequest:
    def test_resolve_unknown_id_returns_false(self, manager):
        assert manager.resolve_request({"request_id": "ghost"}) is False

    def test_resolve_without_id_returns_false(self, manager):
        assert manager.resolve_request({}) is False


class TestWaitForConnection:
    async def test_returns_true_when_already_connected(self, manager):
        ws = FakeWebSocket()
        manager.register("agent-1", ws)
        assert await manager.wait_for_connection("agent-1", timeout=1.0) is True

    async def test_returns_false_on_timeout_when_offline(self, manager):
        assert await manager.wait_for_connection("missing", timeout=0.05) is False

    async def test_zero_timeout_returns_immediate_state(self, manager):
        # Offline agent + zero timeout — short-circuit, no waiting.
        assert await manager.wait_for_connection("missing", timeout=0) is False
        ws = FakeWebSocket()
        manager.register("a", ws)
        assert await manager.wait_for_connection("a", timeout=0) is True

    async def test_wakes_when_agent_connects(self, manager):
        ws = FakeWebSocket()

        async def connect_after_delay():
            await asyncio.sleep(0.02)
            manager.register("agent-1", ws)

        asyncio.create_task(connect_after_delay())
        connected = await manager.wait_for_connection("agent-1", timeout=1.0)
        assert connected is True


class TestSendRequestWaitForAgent:
    async def test_send_request_waits_for_reconnect(self, manager):
        """If wait_for_agent>0 and the agent reconnects in time, send_request succeeds."""
        ws = FakeWebSocket()

        async def connect_then_respond():
            await asyncio.sleep(0.02)
            manager.register("agent-1", ws)
            # Wait until send_request has actually pushed the payload
            for _ in range(50):
                if ws.sent:
                    break
                await asyncio.sleep(0.01)
            request_id = json.loads(ws.sent[0])["request_id"]
            manager.resolve_request({"request_id": request_id, "ok": True})

        asyncio.create_task(connect_then_respond())
        result = await manager.send_request(
            "agent-1", "exec", {}, timeout=2.0, wait_for_agent=1.0,
        )
        assert result["ok"] is True

    async def test_send_request_offline_raises_after_wait_expires(self, manager):
        with pytest.raises(AgentOfflineError):
            await manager.send_request(
                "ghost", "exec", {}, timeout=1.0, wait_for_agent=0.05,
            )

