"""Unit tests for AgentConnectionManager.

Targets the in-process state machine: register/unregister, request/response
correlation, timeout, and the offline-cancel guarantee that pending Futures
get failed when an agent disconnects.

These tests use a tiny FakeWebSocket so they don't require an HTTP server.
"""

import asyncio
import contextlib
import json
from typing import Any

import pytest

from app.services.agent_manager import (
    AgentConnectionManager,
    AgentOfflineError,
    AgentShuttingDownError,
    CommandTimeoutError,
)


class FakeWebSocket:
    """Minimal WebSocket double — records every send_text payload."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_next = False
        self.closed: tuple[int, str] | None = None

    async def send_text(self, payload: str) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("send failed")
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


@pytest.fixture
def manager() -> AgentConnectionManager:
    return AgentConnectionManager()


class TestRegisterUnregister:
    async def test_register_first_connection(self, manager):
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)
        assert manager.is_connected("agent-1")
        assert "agent-1" in manager.get_connected_agent_ids()

    async def test_register_replaces_existing_connection(self, manager):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await manager.register("agent-1", ws1)
        await manager.register("agent-1", ws2)
        # register() now owns the close lifecycle of the replaced ws.
        assert ws1.closed is not None
        assert ws1.closed[0] == 1012
        assert manager.is_connected("agent-1")

    async def test_unregister_removes_when_ws_matches(self, manager):
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)
        await manager.unregister("agent-1", ws)
        assert not manager.is_connected("agent-1")

    async def test_unregister_with_stale_ws_is_noop(self, manager):
        """A stale handler must not delete a newer reconnection."""
        old_ws = FakeWebSocket()
        new_ws = FakeWebSocket()
        await manager.register("agent-1", old_ws)
        await manager.register("agent-1", new_ws)  # reconnect

        await manager.unregister("agent-1", old_ws)  # late cleanup from old handler
        assert manager.is_connected("agent-1")  # newer connection still alive

    async def test_unregister_without_ws_force_removes(self, manager):
        """delete_agent passes ws=None to unconditionally remove."""
        await manager.register("agent-1", FakeWebSocket())
        await manager.unregister("agent-1")
        assert not manager.is_connected("agent-1")


class TestSendRequest:
    async def test_send_request_offline_raises(self, manager):
        with pytest.raises(AgentOfflineError):
            await manager.send_request("missing", "exec", {})

    async def test_send_request_uses_full_128bit_request_id(self, manager):
        """Regression: request_id MUST be a full 128-bit UUID hex (32 chars).

        The WebSocket dispatcher routes responses purely by request_id now
        (the type-whitelist was removed after the envelope-shadowing bug
        fixed 2026-04-08), so a short / predictable id would let one
        in-flight request's Future be resolved by another response. Do
        not allow shortening this back to ``hex[:12]``.
        """
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        async def respond_after_delay():
            await asyncio.sleep(0.01)
            outbound = json.loads(ws.sent[0])
            request_id = outbound["request_id"]
            # Full UUID hex is exactly 32 chars (128 bits) and lowercase hex only.
            assert len(request_id) == 32
            int(request_id, 16)  # must parse as hex
            # Outbound envelope must nest user data under ``payload``.
            assert outbound["payload"] == {}
            assert outbound["type"] == "exec"
            manager.resolve_request({
                "type": "exec_result",
                "request_id": request_id,
                "payload": {"ok": True},
            })

        responder = asyncio.create_task(respond_after_delay())
        await manager.send_request("agent-1", "exec", {}, timeout=2.0)
        await responder

    async def test_send_request_success(self, manager):
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        async def respond_after_delay():
            # Give send_request time to register the future, then resolve it
            await asyncio.sleep(0.01)
            outbound = json.loads(ws.sent[0])
            request_id = outbound["request_id"]
            # Outbound: caller payload is nested.
            assert outbound["payload"] == {"command": "echo hi"}
            manager.resolve_request({
                "type": "exec_result",
                "request_id": request_id,
                "payload": {
                    "exit_code": 0,
                    "stdout": "hello",
                },
            })

        responder = asyncio.create_task(respond_after_delay())
        result = await manager.send_request(
            "agent-1", "exec", {"command": "echo hi"}, timeout=2.0
        )
        await responder

        # send_request unwraps payload before returning, so MCP tools
        # see a flat dict.
        assert result["exit_code"] == 0
        assert result["stdout"] == "hello"

    async def test_send_request_runtime_error_on_error_payload(self, manager):
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        async def respond_with_error():
            await asyncio.sleep(0.01)
            request_id = json.loads(ws.sent[0])["request_id"]
            manager.resolve_request({
                "type": "exec_result",
                "request_id": request_id,
                "payload": {"error": "command not found"},
            })

        asyncio.create_task(respond_with_error())
        with pytest.raises(RuntimeError, match="command not found"):
            await manager.send_request("agent-1", "exec", {"command": "boom"}, timeout=2.0)

    async def test_send_request_missing_payload_raises(self, manager):
        """Wire-contract guard: an envelope without ``payload`` is a bug.

        Don't silently substitute an empty dict — surface the broken
        agent so operators can identify which handler regressed.
        """
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        async def respond_flat():
            await asyncio.sleep(0.01)
            request_id = json.loads(ws.sent[0])["request_id"]
            # Old flat format — must be rejected loudly.
            manager.resolve_request({
                "type": "exec_result",
                "request_id": request_id,
                "exit_code": 0,
            })

        asyncio.create_task(respond_flat())
        with pytest.raises(RuntimeError, match="missing ``payload``"):
            await manager.send_request("agent-1", "exec", {}, timeout=2.0)

    async def test_send_request_timeout(self, manager):
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        with pytest.raises(CommandTimeoutError):
            await manager.send_request("agent-1", "exec", {}, timeout=0.05)

    async def test_disconnect_cancels_pending_request(self, manager):
        """When an agent goes offline mid-request, the future must fail."""
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        async def disconnect_soon():
            await asyncio.sleep(0.01)
            await manager.unregister("agent-1", ws)

        asyncio.create_task(disconnect_soon())
        with pytest.raises(AgentOfflineError):
            await manager.send_request("agent-1", "exec", {}, timeout=2.0)


class TestSendRaw:
    async def test_send_raw_writes_json(self, manager):
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)
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
        await manager.register("agent-1", ws)
        assert await manager.wait_for_connection("agent-1", timeout=1.0) is True

    async def test_returns_false_on_timeout_when_offline(self, manager):
        assert await manager.wait_for_connection("missing", timeout=0.05) is False

    async def test_zero_timeout_returns_immediate_state(self, manager):
        # Offline agent + zero timeout — short-circuit, no waiting.
        assert await manager.wait_for_connection("missing", timeout=0) is False
        ws = FakeWebSocket()
        await manager.register("a", ws)
        assert await manager.wait_for_connection("a", timeout=0) is True

    async def test_wakes_when_agent_connects(self, manager):
        ws = FakeWebSocket()

        async def connect_after_delay():
            await asyncio.sleep(0.02)
            await manager.register("agent-1", ws)

        task = asyncio.create_task(connect_after_delay())
        connected = await manager.wait_for_connection("agent-1", timeout=1.0)
        await task
        assert connected is True


class TestSendRequestWaitForAgent:
    async def test_send_request_waits_for_reconnect(self, manager):
        """If wait_for_agent>0 and the agent reconnects in time, send_request succeeds."""
        ws = FakeWebSocket()

        async def connect_then_respond():
            await asyncio.sleep(0.02)
            await manager.register("agent-1", ws)
            # Wait until send_request has actually pushed the payload
            for _ in range(50):
                if ws.sent:
                    break
                await asyncio.sleep(0.01)
            request_id = json.loads(ws.sent[0])["request_id"]
            manager.resolve_request({
                "type": "exec_result",
                "request_id": request_id,
                "payload": {"ok": True},
            })

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


# ──────────────────────────────────────────────
# Back-pressure + atomic register regressions (task 69d62a6b)
# ──────────────────────────────────────────────


class TestBackPressure:
    """Per-agent concurrency caps + send lock + pending admission control."""

    async def test_send_lock_serialises_concurrent_sends(self, manager):
        """``send_text`` must be called under a per-agent lock so JSON
        frames from parallel senders cannot interleave."""
        import app.services.agent_manager as am_module

        calls: list[str] = []

        class SerialisingWS:
            async def send_text(self, payload: str) -> None:
                calls.append("start")
                await asyncio.sleep(0.02)
                calls.append("end")

        ws = SerialisingWS()
        await manager.register("agent-1", ws)  # type: ignore[arg-type]

        # Patch the semaphore cap wide open so the test measures the
        # send lock, not the in-flight cap.
        sem = asyncio.Semaphore(10)
        manager._inflight_semaphores["agent-1"] = sem  # type: ignore[attr-defined]

        async def do_send(i: int):
            # Use send_raw so the test doesn't depend on response
            # correlation — it's the send lock we're verifying.
            await manager.send_raw("agent-1", {"i": i})

        await asyncio.gather(do_send(1), do_send(2), do_send(3))
        # With a proper lock the calls MUST appear strictly paired:
        # start/end/start/end/start/end. Interleaving would produce
        # start/start somewhere.
        assert calls == ["start", "end", "start", "end", "start", "end"]

    async def test_pending_cap_rejects_overload(self, manager, monkeypatch):
        """Once ``MAX_PENDING_PER_AGENT`` is reached, new send_request
        calls raise :class:`AgentBusyError` instead of enqueuing."""
        import app.services.agent_manager as am_module
        monkeypatch.setattr(am_module, "MAX_PENDING_PER_AGENT", 2)

        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        # Artificially inflate pending_count to simulate 2 in-flight
        # requests; the cap check uses this dict directly.
        manager._pending_count["agent-1"] = 2  # type: ignore[attr-defined]

        from app.services.agent_manager import AgentBusyError
        with pytest.raises(AgentBusyError):
            await manager.send_request("agent-1", "exec", {}, timeout=0.5)

    async def test_inflight_semaphore_caps_concurrency(self, manager, monkeypatch):
        """At most ``MAX_INFLIGHT_PER_AGENT`` send_request calls can
        actually be mid-send on an agent at any one time."""
        import app.services.agent_manager as am_module
        monkeypatch.setattr(am_module, "MAX_INFLIGHT_PER_AGENT", 2)

        peak = 0
        active = 0
        gate = asyncio.Event()

        class SlowWS:
            async def send_text(self, payload: str) -> None:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await gate.wait()
                active -= 1

        ws = SlowWS()
        await manager.register("agent-1", ws)  # type: ignore[arg-type]

        async def one_request():
            # Each send_request will block on gate.wait() inside send_text,
            # then get a CommandTimeoutError because nobody resolves the
            # future. That's fine — we're measuring concurrency, not
            # success.
            with contextlib.suppress(Exception):
                await manager.send_request(
                    "agent-1", "exec", {}, timeout=0.2,
                )

        # Kick off 5 requests — only 2 should be in send_text at a time.
        tasks = [asyncio.create_task(one_request()) for _ in range(5)]
        await asyncio.sleep(0.05)
        assert peak <= 2
        gate.set()
        await asyncio.gather(*tasks)


class TestAtomicRegister:
    """Replace semantics: old ws is closed, old ping_task cancelled,
    pending futures flushed."""

    async def test_replace_closes_old_ws_and_cancels_ping_task(self, manager):
        old_ws = FakeWebSocket()
        new_ws = FakeWebSocket()

        # Dummy ping task on the old connection.
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def fake_ping():
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                stopped.set()
                raise

        ping_task = asyncio.create_task(fake_ping())
        await manager.register("agent-1", old_ws, ping_task=ping_task)
        # Ensure the ping task has actually started running before
        # we trigger the replace, otherwise cancel() would abort it
        # before the ``except CancelledError`` block gets a chance.
        await started.wait()

        # Reconnect: register a new ws.
        await manager.register("agent-1", new_ws)

        assert old_ws.closed == (1012, "Replaced by new connection")
        assert ping_task.cancelled() or ping_task.done()
        assert stopped.is_set()
        assert manager.is_connected("agent-1")

    async def test_replace_flushes_pending_futures_with_offline(self, manager):
        old_ws = FakeWebSocket()
        await manager.register("agent-1", old_ws)

        async def long_request():
            with pytest.raises(AgentOfflineError):
                await manager.send_request(
                    "agent-1", "exec", {}, timeout=5.0,
                )

        # Start a request that waits for response
        task = asyncio.create_task(long_request())
        await asyncio.sleep(0.02)  # let the future register

        # Now reconnect — the old pending future must be flushed.
        new_ws = FakeWebSocket()
        await manager.register("agent-1", new_ws)

        await asyncio.wait_for(task, timeout=1.0)


class TestPrometheusMetrics:
    """Smoke tests for the Prometheus instrumentation in agent_manager.

    These tests touch the global default registry, so they assert on
    relative deltas (before/after) rather than absolute values — that
    way other tests in the suite that also exercise agent_manager do
    not cause spurious failures here.
    """

    async def test_connection_gauge_tracks_register_unregister(self, manager):
        from app.core.metrics import agent_connections

        before = agent_connections._value.get()  # type: ignore[attr-defined]
        ws = FakeWebSocket()
        await manager.register("metrics-agent", ws)
        assert agent_connections._value.get() == before + 1  # type: ignore[attr-defined]

        await manager.unregister("metrics-agent", ws)
        assert agent_connections._value.get() == before  # type: ignore[attr-defined]

    async def test_reconnect_does_not_double_count_connections(self, manager):
        """Replacing a connection must not bump the gauge twice."""
        from app.core.metrics import agent_connections

        before = agent_connections._value.get()  # type: ignore[attr-defined]
        await manager.register("metrics-agent", FakeWebSocket())
        await manager.register("metrics-agent", FakeWebSocket())  # reconnect
        assert agent_connections._value.get() == before + 1  # type: ignore[attr-defined]

        await manager.unregister("metrics-agent")
        assert agent_connections._value.get() == before  # type: ignore[attr-defined]

    async def test_request_duration_recorded_on_success(self, manager):
        from app.core.metrics import agent_request_duration_seconds

        sample_before = agent_request_duration_seconds.labels(op="exec")._sum.get()  # type: ignore[attr-defined]

        ws = FakeWebSocket()
        await manager.register("metrics-agent", ws)

        async def respond():
            await asyncio.sleep(0.01)
            request_id = json.loads(ws.sent[0])["request_id"]
            manager.resolve_request({
                "type": "exec_result",
                "request_id": request_id,
                "payload": {"exit_code": 0},
            })

        responder = asyncio.create_task(respond())
        await manager.send_request("metrics-agent", "exec", {}, timeout=2.0)
        await responder

        sample_after = agent_request_duration_seconds.labels(op="exec")._sum.get()  # type: ignore[attr-defined]
        assert sample_after > sample_before

    async def test_offline_increments_error_counter(self, manager):
        from app.core.metrics import agent_request_errors_total

        before = agent_request_errors_total.labels(reason="offline")._value.get()  # type: ignore[attr-defined]
        with pytest.raises(AgentOfflineError):
            await manager.send_request("ghost", "exec", {})
        after = agent_request_errors_total.labels(reason="offline")._value.get()  # type: ignore[attr-defined]
        assert after == before + 1


class TestGracefulShutdown:
    """Verifies the start_shutdown / drain contract used by lifespan."""

    async def test_drain_returns_true_when_idle(self, manager):
        """A fresh manager has nothing to drain."""
        assert await manager.drain(timeout=0.1) is True

    async def test_send_request_rejected_after_shutdown(self, manager):
        """Once start_shutdown is called, send_request must refuse new work."""
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)
        manager.start_shutdown()

        with pytest.raises(AgentShuttingDownError):
            await manager.send_request("agent-1", "exec", {}, timeout=1.0)

    async def test_send_raw_rejected_after_shutdown(self, manager):
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)
        manager.start_shutdown()

        with pytest.raises(AgentShuttingDownError):
            await manager.send_raw("agent-1", {"hi": 1})

    async def test_drain_waits_for_in_flight_request(self, manager):
        """drain() must block until the in-flight request completes."""
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        # Kick off a request whose Future will not resolve until we
        # explicitly resolve it below.
        send_task = asyncio.create_task(
            manager.send_request("agent-1", "exec", {}, timeout=5.0)
        )
        await asyncio.sleep(0.02)  # let the future register

        # start_shutdown blocks new admissions but the in-flight one
        # is still counted in pending — drain must wait.
        manager.start_shutdown()
        drain_task = asyncio.create_task(manager.drain(timeout=2.0))
        await asyncio.sleep(0.02)
        assert not drain_task.done()

        # Resolve the pending request → drain should wake.
        request_id = json.loads(ws.sent[0])["request_id"]
        manager.resolve_request({
            "type": "exec_result",
            "request_id": request_id,
            "payload": {"exit_code": 0},
        })

        await asyncio.wait_for(send_task, timeout=1.0)
        result = await asyncio.wait_for(drain_task, timeout=1.0)
        assert result is True

    async def test_drain_returns_false_on_timeout(self, manager):
        """If a request never completes, drain reports timeout."""
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)

        # Send a request that nobody will ever resolve.
        send_task = asyncio.create_task(
            manager.send_request("agent-1", "exec", {}, timeout=10.0)
        )
        await asyncio.sleep(0.02)

        manager.start_shutdown()
        result = await manager.drain(timeout=0.1)
        assert result is False

        # Cleanup so the task doesn't leak past the test. Awaiting a
        # cancelled task raises CancelledError; that is the *only*
        # exception we want to swallow here — anything else means the
        # send path itself is broken and the test should report it.
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await send_task

    async def test_shutdown_increments_error_counter(self, manager):
        from app.core.metrics import agent_request_errors_total

        before = agent_request_errors_total.labels(reason="shutting_down")._value.get()  # type: ignore[attr-defined]
        ws = FakeWebSocket()
        await manager.register("agent-1", ws)
        manager.start_shutdown()
        with pytest.raises(AgentShuttingDownError):
            await manager.send_request("agent-1", "exec", {}, timeout=1.0)
        after = agent_request_errors_total.labels(reason="shutting_down")._value.get()  # type: ignore[attr-defined]
        assert after == before + 1


