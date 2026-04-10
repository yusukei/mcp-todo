"""Tier 2 real-Redis integration tests for the multi-worker agent bus.

Fakeredis pub/sub is broken in our test env — ``pubsub.get_message``
returns ``None`` after a subscribe + publish round-trip in the same
process — so the Tier 1 unit suite in ``tests/unit/test_agent_bus.py``
cannot verify the following end-to-end paths:

- Cross-worker request/response round-trip through Redis pub/sub
  (``A.send_request`` → ``B.dispatch`` → response publish →
  ``A.subscribe receives``)
- Connect-broadcast wake (``B.register`` → ``agent:events`` publish →
  ``A`` wakes ``wait_for_connection``)
- Disconnect-broadcast registry prune
- Heartbeat TTL expiry observed by a peer worker
- Subscribe-before-publish ordering guarantee under repeated load

This file spins up a real Redis via ``testcontainers[redis]`` and
exercises the full round-trip across two ``AgentConnectionManager``
instances in the same process. The whole module is skipped
automatically if Docker is unavailable, so CI environments without
a Docker daemon stay green.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# ── Module-level skip guard ─────────────────────────────────────
#
# We probe Docker at import time so the skip is loud in the
# collection output rather than each test failing individually
# inside the ``real_redis`` fixture. The two failure modes we
# expect on dev boxes are: docker-py not installed (older dev
# image) and Docker daemon not running (forgot to start Docker
# Desktop on Windows).
_skip_reason: str | None = None
try:
    import docker as _docker  # type: ignore[import-not-found]

    _docker.from_env().ping()
except ImportError as _e:
    _skip_reason = f"docker-py not installed: {_e}"
except Exception as _e:  # pragma: no cover - env-dependent
    _skip_reason = f"Docker daemon unreachable: {type(_e).__name__}: {_e}"

if _skip_reason is not None:
    pytest.skip(_skip_reason, allow_module_level=True)

pytest.importorskip(
    "testcontainers.core.container",
    reason="testcontainers not installed",
)

import pytest_asyncio  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402

import app.core.redis as _redis_module  # noqa: E402
from app.services import agent_bus as _agent_bus_module  # noqa: E402
from app.services.agent_manager import AgentConnectionManager  # noqa: E402


# Force every async test + async fixture in this module onto a
# fresh per-test event loop. The project-wide default set by
# ``asyncio_default_fixture_loop_scope = "session"`` in
# ``pyproject.toml`` normally shares one loop between fixtures and
# tests, but that interacts badly with redis-py: its
# ``asyncio.Lock`` objects inside the connection pool capture the
# loop they were first awaited on, so any cross-loop access (e.g.
# fixture runs in session loop, test runs in function loop) trips
# ``RuntimeError: bound to a different event loop``. Forcing both
# fixture and test to the function loop keeps them aligned.
pytestmark = pytest.mark.asyncio(loop_scope="function")


class FakeWebSocket:
    """Minimal WebSocket double used by both workers in each test."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def redis_container_url():
    """Start a real Redis container once per module; yield its URL.

    Sync fixture (no event loop) so the container lifecycle is
    completely decoupled from the per-test event loops managed by
    pytest-asyncio. A single container is reused across the module's
    tests to avoid paying the image-pull + start cost on every test.

    Implementation notes:

    - We use the generic ``DockerContainer`` instead of
      ``testcontainers.redis.RedisContainer`` because the latter's
      built-in wait strategy hangs on Docker Desktop for Windows —
      it tries to handshake via ``redis-py`` against an address its
      own ``get_container_host_ip()`` returns, and if that host is
      ``localhost`` on Windows the IPv6 path resets the connection.
      Rolling our own socket probe below avoids the issue.
    - We force the host to ``127.0.0.1`` when testcontainers returns
      ``localhost``: on Win10/11 ``localhost`` resolves to ``::1``
      first, and Docker Desktop's IPv6 port forwarding for
      dynamic-port bindings is broken (verified by side-by-side
      test — same port, ``localhost`` resets, ``127.0.0.1`` works).
    """
    from testcontainers.core.container import DockerContainer

    container = DockerContainer("redis:7-alpine").with_exposed_ports(6379)
    container.start()
    try:
        host = container.get_container_host_ip()
        if host == "localhost":
            host = "127.0.0.1"
        port = int(container.get_exposed_port(6379))
        url = f"redis://{host}:{port}/0"

        # Wait up to 30s for Redis to answer a raw-socket PING.
        # Using a plain socket (not redis-py) avoids the handshake
        # ordering fragility observed on Windows and keeps this
        # fixture event-loop-free.
        import socket
        import time

        deadline = time.monotonic() + 30.0
        while True:
            try:
                with socket.create_connection((host, port), timeout=2) as s:
                    s.sendall(b"PING\r\n")
                    reply = s.recv(64)
                    if b"PONG" in reply:
                        break
            except Exception:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Redis container at {url} did not answer PING in 30s"
                )
            time.sleep(0.2)

        yield url
    finally:
        container.stop()


@pytest_asyncio.fixture(loop_scope="function")
async def real_redis(redis_container_url):
    """Yield an aioredis client bound to the current test's loop.

    Event-loop scoping rule: pytest-asyncio gives every test a fresh
    loop by default, and ``asyncio.Lock`` objects inside redis-py
    remember the loop they were created on. If we kept this client
    as a module-scoped fixture, the second test in the module would
    trip on ``RuntimeError: <Lock> is bound to a different event
    loop``. Creating a new client inside each test's own loop (while
    the container itself is still reused) is the cleanest fix.

    The client is swapped into ``app.core.redis._client`` so every
    caller that reaches the bus via ``get_redis()`` — including
    ``AgentConnectionManager.start()`` and its background listeners
    — talks to the real container for the duration of the test.
    """
    # ``health_check_interval`` + ``socket_keepalive`` keep the
    # BLPOP/pubsub idle connections alive through Docker Desktop's
    # port-forwarding layer on Windows.
    client = aioredis.from_url(
        redis_container_url,
        decode_responses=True,
        health_check_interval=10,
        socket_keepalive=True,
    )
    # Flush before the test so any residue from a previous test in
    # the module (which shared the container) is gone.
    await client.flushdb()

    original_client = _redis_module._client
    _redis_module._client = client
    try:
        yield client
    finally:
        _redis_module._client = original_client
        await client.aclose()


@pytest_asyncio.fixture(loop_scope="function")
async def two_workers(real_redis):
    """Yield ``(worker_a, worker_b)`` bound to the real Redis container.

    The root conftest's autouse ``reset_db`` fixture flushes Redis
    before every test (via ``get_redis()``, which now points at
    the real container) so no explicit cleanup is needed here.
    """
    a = AgentConnectionManager(worker_id="worker-A")
    b = AgentConnectionManager(worker_id="worker-B")
    await a.start()
    await b.start()
    # Give the events/command listeners one tick to subscribe.
    # Without this sleep, a very fast test could publish a connect
    # broadcast before B's events pubsub has acknowledged the
    # SUBSCRIBE, and Redis pub/sub has no buffering.
    await asyncio.sleep(0.1)
    try:
        yield a, b
    finally:
        await a.stop()
        await b.stop()


# ── Cross-worker request/response round-trip ────────────────────


class TestCrossWorkerRoundTrip:
    """A → bus → B → response → A, end-to-end through real pub/sub."""

    async def test_send_request_remote_round_trip(self, two_workers, real_redis):
        """B owns the agent; A.send_request must bounce through Redis."""
        a, b = two_workers
        ws = FakeWebSocket()
        await b.register("agent-remote", ws)

        # Sanity check: the bus on both managers should be wired to
        # the real Redis container, not the session-wide fakeredis.
        assert a._bus._redis is real_redis, "worker A bus not bound to real redis"
        assert b._bus._redis is real_redis, "worker B bus not bound to real redis"

        # A has no local record of agent-remote — it must discover
        # the registry entry published by B and route via the bus.
        assert await a.wait_for_connection("agent-remote", timeout=2.0)

        async def responder() -> None:
            # Poll ws.sent for the request the bus just delivered
            # to B's local transport, then resolve it so A's
            # awaiting send_request_remote can complete.
            #
            # Budget: 15s. BLPOP has a 1s tick, first-container
            # cold start can add another second, and Docker Desktop
            # port-forwarding adds variable overhead on Windows, so
            # a 2s budget has proven too tight.
            for _ in range(750):
                if ws.sent:
                    req = json.loads(ws.sent[-1])
                    b.resolve_request({
                        "type": "exec_result",
                        "request_id": req["request_id"],
                        "payload": {"exit_code": 0, "stdout": "from-B"},
                    })
                    return
                await asyncio.sleep(0.02)
            raise AssertionError("responder never saw a request on ws.sent")

        responder_task = asyncio.create_task(responder())
        try:
            result = await a.send_request(
                "agent-remote", "exec", {"command": "ls"}, timeout=15.0,
            )
        except BaseException:
            # Show the ORIGINAL send_request failure instead of the
            # masking AssertionError from the responder that gave up
            # waiting because send_request never delivered anything.
            responder_task.cancel()
            try:
                await responder_task
            except BaseException:
                pass
            raise
        await responder_task

        assert result == {"exit_code": 0, "stdout": "from-B"}

    async def test_repeated_round_trips_no_loss(self, two_workers):
        """Subscribe-before-publish ordering must not drop any response.

        This stresses the race window called out in the
        ``agent_bus`` module docstring: if ``send_request_remote``
        ever rpushed the command envelope before completing its
        pubsub.subscribe, a fast owner could publish the response
        into the void and the caller would time out. Running the
        round-trip ten times in quick succession surfaces any such
        dropped response as a ``CommandTimeoutError``.
        """
        a, b = two_workers
        ws = FakeWebSocket()
        await b.register("agent-loop", ws)
        assert await a.wait_for_connection("agent-loop", timeout=2.0)

        async def responder() -> None:
            seen = 0
            # Cap the loop so a bug doesn't hang the test runner.
            # Budget: ~30s total (1500 * 0.02). Ten round-trips
            # through the real container with its BLPOP tick can
            # take a non-trivial slice of that on Windows.
            for _ in range(1500):
                if len(ws.sent) > seen:
                    req = json.loads(ws.sent[seen])
                    b.resolve_request({
                        "type": "exec_result",
                        "request_id": req["request_id"],
                        "payload": {"n": seen},
                    })
                    seen += 1
                    if seen >= 10:
                        return
                else:
                    await asyncio.sleep(0.02)
            raise AssertionError(
                f"responder saw only {seen}/10 requests on ws.sent"
            )

        responder_task = asyncio.create_task(responder())
        try:
            results: list[dict] = []
            for _ in range(10):
                r = await a.send_request(
                    "agent-loop", "exec", {}, timeout=15.0,
                )
                results.append(r)
        finally:
            await responder_task

        assert [r["n"] for r in results] == list(range(10))


# ── Connect / disconnect broadcasts ─────────────────────────────


class TestConnectBroadcast:
    async def test_connect_event_wakes_wait_for_connection(self, two_workers):
        """B.register → events publish → A's wait_for_connection wakes.

        Schedules ``b.register`` *after* ``a.wait_for_connection``
        begins awaiting, so the test only passes if the
        cross-worker ``agent:events`` broadcast actually reaches
        A's events listener and forwards the wake into the local
        transport's connect-waiter set.
        """
        a, b = two_workers

        async def late_register() -> None:
            # Delay just long enough for A to enter wait_for_connection.
            await asyncio.sleep(0.1)
            await b.register("agent-late", FakeWebSocket())

        reg_task = asyncio.create_task(late_register())
        try:
            result = await a.wait_for_connection("agent-late", timeout=3.0)
        finally:
            await reg_task

        assert result is True


class TestDisconnectBroadcast:
    async def test_unregister_clears_remote_view(self, two_workers):
        """B.unregister must make A.is_connected_anywhere return False."""
        a, b = two_workers
        ws = FakeWebSocket()
        await b.register("agent-bye", ws)
        assert await a.is_connected_anywhere("agent-bye") is True

        await b.unregister("agent-bye", ws)
        # The registry DEL is synchronous in on_local_unregister,
        # but give the events listener a tick in case a future
        # refactor moves the delete to the broadcast handler.
        await asyncio.sleep(0.05)
        assert await a.is_connected_anywhere("agent-bye") is False


# ── Heartbeat / TTL expiry ──────────────────────────────────────


class TestHeartbeatExpiry:
    async def test_registry_entry_expires_when_owner_stops_heartbeating(
        self, real_redis, monkeypatch,
    ):
        """A crashed owner's registry entry must expire under TTL.

        We simulate the crash by (a) shortening REGISTRY_TTL_SECONDS
        so the wait is tolerable in a unit run, and (b) raising
        HEARTBEAT_INTERVAL_SECONDS well above the shortened TTL so
        the live heartbeat never gets a chance to refresh the key.
        From the peer worker's perspective this is indistinguishable
        from the owner crashing mid-session.
        """
        monkeypatch.setattr(_agent_bus_module, "REGISTRY_TTL_SECONDS", 2)
        monkeypatch.setattr(
            _agent_bus_module, "HEARTBEAT_INTERVAL_SECONDS", 60,
        )

        owner = AgentConnectionManager(worker_id="worker-owner")
        peer = AgentConnectionManager(worker_id="worker-peer")
        await owner.start()
        await peer.start()
        try:
            await owner.register("agent-ephemeral", FakeWebSocket())
            assert await peer.is_connected_anywhere("agent-ephemeral") is True

            # Wait out the TTL. No heartbeat fires (interval=60s)
            # so the key cannot be refreshed.
            await asyncio.sleep(2.5)

            # We must NOT call owner.stop() before this check — that
            # would trigger on_local_unregister and delete the entry
            # explicitly, bypassing the TTL path we're verifying.
            assert (
                await peer.is_connected_anywhere("agent-ephemeral") is False
            )
        finally:
            # The owner still thinks it owns the agent locally; a
            # clean stop() publishes a (now-redundant) disconnect
            # broadcast, which is harmless.
            await owner.stop()
            await peer.stop()
