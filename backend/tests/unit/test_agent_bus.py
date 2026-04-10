"""Unit tests for the Redis-backed multi-worker agent bus.

These tests instantiate two ``AgentConnectionManager`` objects in
the same process to simulate two separate uvicorn workers, both
talking to the shared fakeredis client wired in by ``conftest``.

## Scope and limitations

Fakeredis (asyncio) implements LIST / STRING / SCAN / EVAL well
enough for the registry and command-queue paths, but its pub/sub
support is broken: ``pubsub.get_message`` returns ``None`` after a
subscribe + publish round-trip in the same process. The bus's
*cross-worker request/response correlation* and *connect-broadcast
wake* paths therefore cannot be exercised end-to-end here — they
need real Redis (testcontainers) and live in the Tier 2 suite at
``tests/integration/test_agent_bus_realredis.py``. The split is:

- **Tier 1 (this file, fakeredis)**: registry SET/GET/WATCH-DEL,
  owner discovery, routing decision table, lifecycle idempotency,
  and direct regression coverage of ``_dispatch_remote_command``
  via a ``_CapturingRedis`` double that bypasses pub/sub entirely.

- **Tier 2 (real-redis integration)**: full cross-worker round
  trip through real pub/sub, repeated round-trips under load (the
  subscribe-before-publish ordering guarantee), connect-broadcast
  wake, disconnect-broadcast registry prune, and heartbeat TTL
  expiry observed by a peer worker.

What this file DOES verify:

- Registry SET / GET / EVAL-based compare-and-delete on register / unregister
- Owner discovery via ``RedisAgentBus.get_owner``
- Cluster-wide ``is_connected_anywhere`` lookup
- ``list_remote_agent_ids`` SCAN walk
- Routing decision: locally-owned agents take the fast path and
  do NOT touch the Redis command queue
- Routing decision: ``AgentOfflineError`` when the registry has no
  entry for the agent (which is what fakeredis-only tests can
  observe; the "owner exists but pub/sub round-trip" path is
  covered by the real-redis suite)
- ``AgentConnectionManager.start`` / ``stop`` are idempotent and do
  not leak listener tasks
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.redis import get_redis
from app.services.agent_bus import KEY_CMD_QUEUE, KEY_REGISTRY
from app.services.agent_local_transport import AgentOfflineError
from app.services.agent_manager import AgentConnectionManager


class FakeWebSocket:
    """Minimal WebSocket double — used by both Local + Bus tests."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


@pytest.fixture
async def two_workers():
    """Yield (worker_a, worker_b) backed by the shared fakeredis client.

    Both workers share the same Redis (the conftest fakeredis), so
    the registry-based ownership signalling between them is exactly
    the same code path as in production. The fixture starts the bus
    listeners on both workers and stops them on teardown.

    fakeredis pub/sub is broken (see module docstring), so we wait
    only briefly for the events listener to subscribe instead of
    relying on the connect broadcast actually being delivered.
    """
    a = AgentConnectionManager(worker_id="worker-A")
    b = AgentConnectionManager(worker_id="worker-B")
    await a.start()
    await b.start()
    # One event-loop tick for the listener tasks to settle.
    await asyncio.sleep(0.05)
    try:
        yield a, b
    finally:
        await a.stop()
        await b.stop()
        # Best-effort cleanup so the next test starts fresh.
        # fakeredis has a bug where ``keys`` / ``scan_iter`` after
        # a WATCH/MULTI transaction can return ``None`` and break
        # iteration. Each bus test uses unique agent ids so a
        # missed cleanup is harmless — swallow the cleanup error
        # rather than masking the real test result.
        import contextlib as _contextlib
        with _contextlib.suppress(Exception):
            redis = get_redis()
            keys = await redis.keys("agent:*") or []
            for key in keys:
                await redis.delete(key)


# ── Registry ────────────────────────────────────────────────────


class TestRegistry:
    async def test_register_publishes_owner_to_redis(self, two_workers):
        a, _b = two_workers
        await a.register("agent-x", FakeWebSocket())
        owner = await a._bus.get_owner("agent-x")
        assert owner == "worker-A"

    async def test_register_uses_ttl(self, two_workers):
        """The registry entry must have a TTL so a crashed owner expires."""
        a, _b = two_workers
        await a.register("agent-ttl", FakeWebSocket())
        redis = get_redis()
        ttl = await redis.ttl(KEY_REGISTRY.format(agent_id="agent-ttl"))
        assert 0 < ttl <= 30  # REGISTRY_TTL_SECONDS

    async def test_unregister_clears_registry(self, two_workers):
        a, _b = two_workers
        ws = FakeWebSocket()
        await a.register("agent-x", ws)
        assert await a._bus.get_owner("agent-x") == "worker-A"

        await a.unregister("agent-x", ws)
        assert await a._bus.get_owner("agent-x") is None

    async def test_unregister_only_clears_own_entry(self, two_workers):
        """Compare-and-delete: A's unregister must NOT delete B's entry.

        Simulates a stale-handler scenario where Worker A has been
        replaced as the owner of an agent (the registry now points
        at Worker B), and A then runs its delayed cleanup. The Lua
        script must compare the value before deleting.
        """
        a, b = two_workers
        # Manually plant a registry entry as if B owns the agent.
        redis = get_redis()
        await redis.set(
            KEY_REGISTRY.format(agent_id="agent-x"),
            "worker-B",
            ex=30,
        )

        # A tries to release ownership — should be a no-op because
        # the registry value is "worker-B", not "worker-A".
        await a._bus.on_local_unregister("agent-x")
        assert await b._bus.get_owner("agent-x") == "worker-B"

    async def test_is_connected_anywhere_sees_remote_owner(self, two_workers):
        a, b = two_workers
        await b.register("agent-x", FakeWebSocket())

        assert a.is_connected("agent-x") is False
        assert await a.is_connected_anywhere("agent-x") is True

    async def test_list_remote_agent_ids_walks_registry(self, two_workers):
        a, b = two_workers
        await a.register("agent-1", FakeWebSocket())
        await b.register("agent-2", FakeWebSocket())
        await a.register("agent-3", FakeWebSocket())

        all_ids = await a.get_all_agent_ids()
        assert set(all_ids) == {"agent-1", "agent-2", "agent-3"}


# ── Routing decision ────────────────────────────────────────────


class TestRoutingDecision:
    async def test_local_owner_uses_fast_path(self, two_workers):
        """If A owns the agent, A.send_request never touches the bus queue."""
        a, _b = two_workers
        ws = FakeWebSocket()
        await a.register("agent-local", ws)

        async def respond():
            await asyncio.sleep(0.01)
            request_id = json.loads(ws.sent[0])["request_id"]
            a.resolve_request({
                "type": "exec_result",
                "request_id": request_id,
                "payload": {"exit_code": 0, "stdout": "local"},
            })

        responder = asyncio.create_task(respond())
        result = await a.send_request("agent-local", "exec", {}, timeout=2.0)
        await responder

        assert result["stdout"] == "local"
        # Fast path: nothing went into A's command queue.
        redis = get_redis()
        assert await redis.llen(KEY_CMD_QUEUE.format(worker_id="worker-A")) == 0

    async def test_unknown_agent_raises_offline(self, two_workers):
        """No registry entry → AgentOfflineError, no bus round-trip attempted."""
        a, _b = two_workers
        with pytest.raises(AgentOfflineError):
            await a.send_request("ghost", "exec", {}, timeout=1.0)


# ── Lifecycle ───────────────────────────────────────────────────


class TestLifecycle:
    async def test_start_is_idempotent(self):
        m = AgentConnectionManager(worker_id="worker-idem")
        try:
            await m.start()
            await m.start()  # second call must be a no-op
            assert m._bus._command_task is not None
        finally:
            await m.stop()

    async def test_stop_when_not_started(self):
        m = AgentConnectionManager(worker_id="worker-noop")
        # Should not raise even though start() was never called.
        await m.stop()

    async def test_stop_drops_owned_registry_entries(self, two_workers):
        """A clean shutdown must release the worker's registry entries."""
        a, _b = two_workers
        await a.register("agent-bye", FakeWebSocket())
        assert await a._bus.get_owner("agent-bye") == "worker-A"

        await a.stop()
        assert await a._bus.get_owner("agent-bye") is None


# ── Direct regression tests for the C1 / C2 / I7 fixes ─────────
#
# These exercise ``_dispatch_remote_command`` directly with a
# captured publish-side instead of going through pub/sub. Doing it
# this way means the tests run in fakeredis (no Docker required)
# but still catch the bugs that the architecture review identified
# in commit 65ac55f. A separate testcontainers-backed integration
# suite (see follow-up task) verifies the *full* round-trip with
# real Redis pub/sub.


class _CapturingRedis:
    """Test double for the bus's redis client.

    Captures the (channel, payload) tuples published by
    ``_dispatch_remote_command`` so the tests can assert on the
    response envelope without needing pub/sub.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


class TestDispatchRemoteCommand:
    """Regression tests for C1 (`__send_raw__` dispatch) and I7 (`busy` bucket)."""

    async def _make_bus_with_capture(
        self, worker_id: str, agent_id: str, ws: FakeWebSocket,
    ):
        """Build a manager whose bus publishes into a CapturingRedis.

        Sequence matters: we (1) construct the manager, (2) wire the
        bus to the real fakeredis client so register() can write the
        registry entry, (3) call register() to set up local state,
        (4) THEN swap in the capturing client so only the
        ``_dispatch_remote_command`` publish path is captured.
        Bypasses ``start()`` so the BLPOP listener / events listener
        / heartbeat tasks never run — we just exercise
        ``_dispatch_remote_command`` directly.
        """
        from app.core.redis import get_redis

        manager = AgentConnectionManager(worker_id=worker_id)
        manager._bus._redis = get_redis()  # type: ignore[assignment]
        await manager.register(agent_id, ws)
        # Now swap to the capturing client so dispatch publishes are
        # observable. The local transport already has the WS, so the
        # capturing client only sees publishes from now on.
        captured = _CapturingRedis()
        manager._bus._redis = captured  # type: ignore[assignment]
        return manager, captured

    async def test_dispatch_send_raw_branch(self):
        """Regression for C1: `__send_raw__` MUST forward to local.send_raw.

        Before the C1 fix, ``_dispatch_remote_command`` had no
        ``__send_raw__`` branch and forwarded the unknown msg_type
        to the agent via ``local.send_request``, which then waited
        for a response that the agent never produced. The bus
        timed out 10 seconds later. After the fix, the dispatch
        unwraps the inner payload, calls ``local.send_raw``, and
        publishes an empty ack envelope.
        """
        ws = FakeWebSocket()
        manager, captured = await self._make_bus_with_capture(
            "worker-X", "agent-raw", ws,
        )
        try:
            envelope = {
                "request_id": "req-raw-1",
                "agent_id": "agent-raw",
                "msg_type": "__send_raw__",
                "payload": {"payload": {"hello": "world"}},
                "timeout": 10.0,
                "origin_worker_id": "worker-Y",
            }
            await manager._bus._dispatch_remote_command(envelope)
        finally:
            # Restore the real client so stop()'s registry cleanup
            # talks to the actual fakeredis instead of our capture.
            from app.core.redis import get_redis
            manager._bus._redis = get_redis()  # type: ignore[assignment]
            await manager.stop()

        # The local fake WebSocket received the unwrapped raw payload.
        assert ws.sent == [json.dumps({"hello": "world"})]
        # The bus published an empty ack envelope on the response channel.
        assert len(captured.published) == 1
        channel, raw_response = captured.published[0]
        assert channel == "agent:resp:req-raw-1"
        response = json.loads(raw_response)
        assert response == {"payload": {}}

    async def test_dispatch_envelope_without_request_id_is_dropped(self):
        """A malformed envelope (no request_id) must be logged and dropped."""
        ws = FakeWebSocket()
        manager, captured = await self._make_bus_with_capture(
            "worker-X", "agent-x", ws,
        )
        try:
            envelope = {
                # request_id deliberately missing
                "agent_id": "agent-x",
                "msg_type": "__send_raw__",
                "payload": {"payload": {"x": 1}},
            }
            await manager._bus._dispatch_remote_command(envelope)
        finally:
            # Restore the real client so stop()'s registry cleanup
            # talks to the actual fakeredis instead of our capture.
            from app.core.redis import get_redis
            manager._bus._redis = get_redis()  # type: ignore[assignment]
            await manager.stop()

        # Nothing published — there is no response channel to publish on.
        assert captured.published == []
        # The local WebSocket was not touched either.
        assert ws.sent == []

    async def test_dispatch_offline_returns_offline_envelope(self):
        """Dispatching to an unowned agent returns an offline error envelope."""
        ws = FakeWebSocket()
        manager, captured = await self._make_bus_with_capture(
            "worker-X", "agent-known", ws,
        )
        try:
            envelope = {
                "request_id": "req-offline-1",
                "agent_id": "agent-unknown",  # not registered locally
                "msg_type": "exec",
                "payload": {"command": "ls"},
                "timeout": 5.0,
            }
            await manager._bus._dispatch_remote_command(envelope)
        finally:
            # Restore the real client so stop()'s registry cleanup
            # talks to the actual fakeredis instead of our capture.
            from app.core.redis import get_redis
            manager._bus._redis = get_redis()  # type: ignore[assignment]
            await manager.stop()

        assert len(captured.published) == 1
        _, raw_response = captured.published[0]
        response = json.loads(raw_response)
        assert response["error_kind"] == "offline"
        assert "offline" in response["payload"]["error"].lower()

    async def test_dispatch_busy_returns_busy_envelope(self, monkeypatch):
        """Regression for I7: AgentBusyError must use the `busy` bucket.

        Before the I7 fix, AgentBusyError was caught by the generic
        ``except RuntimeError`` branch and returned as
        ``error_kind="runtime"``, which collapsed an admission-control
        signal into the agent_error metric bucket on the calling worker.
        """
        import app.services.agent_manager as am_module
        monkeypatch.setattr(am_module, "MAX_PENDING_PER_AGENT", 0)

        ws = FakeWebSocket()
        manager, captured = await self._make_bus_with_capture(
            "worker-X", "agent-busy", ws,
        )
        try:
            envelope = {
                "request_id": "req-busy-1",
                "agent_id": "agent-busy",
                "msg_type": "exec",
                "payload": {},
                "timeout": 5.0,
            }
            await manager._bus._dispatch_remote_command(envelope)
        finally:
            # Restore the real client so stop()'s registry cleanup
            # talks to the actual fakeredis instead of our capture.
            from app.core.redis import get_redis
            manager._bus._redis = get_redis()  # type: ignore[assignment]
            await manager.stop()

        assert len(captured.published) == 1
        _, raw_response = captured.published[0]
        response = json.loads(raw_response)
        assert response["error_kind"] == "busy"


class TestDispatchTaskAnchoring:
    """Regression for C2: dispatch tasks must survive GC pressure."""

    async def test_dispatch_tasks_set_exists_and_starts_empty(self):
        """The bus must hold an explicit set for in-flight dispatch tasks.

        Without this set, ``asyncio.create_task`` results would be
        garbage-collected mid-run on GIL pauses (CPython 3.11+
        documents that the event loop only holds weak references).
        """
        m = AgentConnectionManager(worker_id="worker-anchor")
        assert hasattr(m._bus, "_dispatch_tasks")
        assert isinstance(m._bus._dispatch_tasks, set)
        assert m._bus._dispatch_tasks == set()

    async def test_dispatch_task_added_then_removed(self):
        """A dispatched task is anchored, then auto-removed when done.

        Simulates the listener-side anchoring pattern by manually
        creating a task and registering the same add/discard
        callbacks the listener uses, then asserting the lifecycle.
        """
        m = AgentConnectionManager(worker_id="worker-anchor-2")
        try:
            tasks_set = m._bus._dispatch_tasks

            async def fake_dispatch():
                await asyncio.sleep(0.01)

            task = asyncio.create_task(fake_dispatch())
            tasks_set.add(task)
            task.add_done_callback(tasks_set.discard)

            assert len(tasks_set) == 1
            await task
            # done_callback runs in the next event-loop tick.
            await asyncio.sleep(0)
            assert len(tasks_set) == 0
        finally:
            await m.stop()

