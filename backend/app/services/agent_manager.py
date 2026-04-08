"""Agent connection manager — process-wide singleton for remote agent WebSockets.

Extracted from `api/v1/endpoints/workspaces/` so that:
- `mcp/tools/remote.py` can import it without lazy imports / circular deps
- `api/v1/endpoints/chat.py` can dispatch agent payloads via a public method
  instead of poking at the private `_connections` dict.

The class is intentionally framework-agnostic regarding HTTP routing — only the
WebSocket protocol surface is referenced (send_text), so it can be unit-tested
with a fake WebSocket.

## Concurrency / back-pressure model (added 2026-04-08)

Each registered agent has three pieces of per-agent state:

- A **send lock** (``asyncio.Lock``) that serialises ``ws.send_text``
  calls. Without it, two coroutines could interleave bytes from
  different JSON frames — WebSocket frames are a stream and the client
  parser would lose sync.
- A **semaphore** (``asyncio.Semaphore(MAX_INFLIGHT_PER_AGENT)``) that
  caps how many ``send_request`` calls can be in-flight against a
  single agent. This bounds worst-case memory / Future count per
  agent and prevents a single runaway caller from drowning the queue.
- A **pending count** (per-agent + global) that rejects new requests
  when either exceeds its cap. Unlike the semaphore, this cap
  includes *waiting* callers so a backlog cannot grow unboundedly if
  every slot is held.

## register() atomicity (added 2026-04-08)

``register`` is now async and holds a per-manager lock while it:

1. Installs the new ws in ``_connections``.
2. Closes the old ws with code 1012.
3. Cancels the old per-agent ``ping_task`` (if the caller passed one).
4. Flushes any pending Futures keyed to this agent with
   :class:`AgentOfflineError`. Those futures belonged to the *old*
   connection and can no longer be answered.
5. Wakes ``wait_for_connection`` waiters.

This order is deliberate: by publishing the new ws first we guarantee
that callers observing a reconnect through ``is_connected`` see the
new state, while any in-flight request stuck on the old connection
gets a deterministic :class:`AgentOfflineError` instead of timing out.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# Per-agent concurrency caps. These are intentionally module-level
# constants rather than Settings so tests can patch them without
# pytest-env gymnastics. The defaults are sized for a single-process
# deployment talking to a handful of agents: each agent can run 8
# concurrent operations, with up to 64 requests queued on top of
# that. Global ceiling caps the worst case across all agents.
MAX_INFLIGHT_PER_AGENT = 8
MAX_PENDING_PER_AGENT = 64
MAX_PENDING_GLOBAL = 512


class AgentOfflineError(Exception):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        super().__init__(f"Agent {agent_id} is offline")


class CommandTimeoutError(Exception):
    def __init__(self, request_id: str, timeout: float):
        self.request_id = request_id
        self.timeout = timeout
        super().__init__(f"Request {request_id} timed out after {timeout}s")


class AgentBusyError(RuntimeError):
    """Raised when per-agent / global pending caps are exceeded.

    Subclasses :class:`RuntimeError` so ``remote.py``'s existing
    ``except RuntimeError`` in ``_send_to_agent`` surfaces it as a
    :class:`ToolError` without extra plumbing.
    """

    def __init__(self, agent_id: str, *, per_agent: int, global_pending: int):
        self.agent_id = agent_id
        self.per_agent = per_agent
        self.global_pending = global_pending
        super().__init__(
            f"Agent {agent_id} is overloaded "
            f"(pending={per_agent} global={global_pending}); try again shortly"
        )


class AgentConnectionManager:
    """Manages agent WebSocket connections and request/response exchanges.

    Single instance per process. All methods run on the same event loop.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        # request_id → (agent_id, Future)
        self._pending: dict[str, tuple[str, asyncio.Future]] = {}
        # agent_id → number of in-flight/queued requests (counts futures
        # in ``_pending`` plus callers blocked on ``_inflight_semaphores``)
        self._pending_count: dict[str, int] = {}
        # agent_id → set[Event] — woken when the agent connects/reconnects
        self._connect_waiters: dict[str, set[asyncio.Event]] = {}
        # Per-agent send lock: serialises ws.send_text for a given agent
        # so JSON frames cannot interleave.
        self._send_locks: dict[str, asyncio.Lock] = {}
        # Per-agent in-flight semaphore: caps concurrent send_request
        # calls to MAX_INFLIGHT_PER_AGENT.
        self._inflight_semaphores: dict[str, asyncio.Semaphore] = {}
        # Ping task registered by the WS handler via register(...).
        # The manager owns its lifetime during atomic replace.
        self._ping_tasks: dict[str, asyncio.Task] = {}
        # Single lock serialising state transitions in register() so
        # close/cancel/flush happen atomically from the callers'
        # observer viewpoint.
        self._register_lock = asyncio.Lock()

    # ── Per-agent state helpers ─────────────────────────────────

    def _get_send_lock(self, agent_id: str) -> asyncio.Lock:
        lock = self._send_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[agent_id] = lock
        return lock

    def _get_inflight_semaphore(self, agent_id: str) -> asyncio.Semaphore:
        sem = self._inflight_semaphores.get(agent_id)
        if sem is None:
            sem = asyncio.Semaphore(MAX_INFLIGHT_PER_AGENT)
            self._inflight_semaphores[agent_id] = sem
        return sem

    def _increment_pending(self, agent_id: str) -> None:
        self._pending_count[agent_id] = self._pending_count.get(agent_id, 0) + 1

    def _decrement_pending(self, agent_id: str) -> None:
        cur = self._pending_count.get(agent_id, 0)
        if cur <= 1:
            self._pending_count.pop(agent_id, None)
        else:
            self._pending_count[agent_id] = cur - 1

    def _global_pending(self) -> int:
        return sum(self._pending_count.values())

    # ── Connection lifecycle ────────────────────────────────────

    async def register(
        self,
        agent_id: str,
        ws: WebSocket,
        *,
        ping_task: asyncio.Task | None = None,
    ) -> None:
        """Register a new connection, atomically tearing down the old one.

        Caller passes the per-connection ``ping_task`` so the manager
        owns its lifetime during replace — this prevents the race where
        a stale ping task writes to a dead socket after the reconnect.

        On replace, the manager:
          1. publishes the new ``ws`` in ``_connections``,
          2. closes the old ``ws`` (best-effort),
          3. cancels the old ``ping_task`` and awaits it so the task
             has actually exited before we continue,
          4. flushes all pending Futures keyed to ``agent_id`` with
             :class:`AgentOfflineError` (they belonged to the old
             connection),
          5. wakes any ``wait_for_connection`` waiters.
        """
        async with self._register_lock:
            old_ws = self._connections.get(agent_id)
            old_ping_task = self._ping_tasks.get(agent_id)

            # (1) Publish new connection immediately so observers see
            # the reconnect before we start the cleanup of the old one.
            self._connections[agent_id] = ws
            if ping_task is not None:
                self._ping_tasks[agent_id] = ping_task
            else:
                self._ping_tasks.pop(agent_id, None)

            # (2) Close the old socket.
            if old_ws is not None and old_ws is not ws:
                logger.warning(
                    "Agent %s: replacing existing connection (reconnect)",
                    agent_id,
                )
                try:
                    await old_ws.close(code=1012, reason="Replaced by new connection")
                except Exception:
                    logger.info(
                        "Agent %s: old ws.close during replace failed "
                        "(likely already dead)",
                        agent_id,
                        exc_info=True,
                    )

            # (3) Cancel & await the old ping task.
            if old_ping_task is not None and old_ping_task is not ping_task:
                old_ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await old_ping_task

            # (4) Flush pending futures from the old connection.
            if old_ws is not None and old_ws is not ws:
                to_flush = [
                    rid for rid, (aid, _) in self._pending.items()
                    if aid == agent_id
                ]
                for rid in to_flush:
                    _, fut = self._pending.pop(rid)
                    if not fut.done():
                        fut.set_exception(AgentOfflineError(agent_id))
                # pending_count is decremented by the send_request
                # finally block; set_exception wakes it so the cleanup
                # runs. No manual decrement here.

            # (5) Wake anybody waiting on wait_for_connection(agent_id).
            waiters = self._connect_waiters.pop(agent_id, None)
            if waiters:
                for event in waiters:
                    event.set()

    async def unregister(
        self, agent_id: str, ws: WebSocket | None = None,
    ) -> None:
        """Unregister a connection.

        If ws is provided, only removes if it matches the current connection
        (prevents a stale handler from removing a newer reconnection).
        If ws is None, unconditionally removes (used by delete_agent /
        rotate-token).
        """
        async with self._register_lock:
            if ws is not None:
                current = self._connections.get(agent_id)
                if current is not ws:
                    # This is a stale handler — the agent already reconnected
                    logger.debug("Agent %s: skipping stale unregister", agent_id)
                    return
            self._connections.pop(agent_id, None)
            # The handler that owned this ws will cancel its own
            # ping_task in its finally clause; we just drop our
            # reference so we don't touch it after replace.
            self._ping_tasks.pop(agent_id, None)
            # Cancel all pending futures for this agent
            to_cancel = [
                rid for rid, (aid, _) in self._pending.items() if aid == agent_id
            ]
            for rid in to_cancel:
                _, fut = self._pending.pop(rid)
                if not fut.done():
                    fut.set_exception(AgentOfflineError(agent_id))

    def is_connected(self, agent_id: str) -> bool:
        return agent_id in self._connections

    def get_connected_agent_ids(self) -> list[str]:
        return list(self._connections.keys())

    async def wait_for_connection(self, agent_id: str, timeout: float) -> bool:
        """Block until ``agent_id`` connects, up to ``timeout`` seconds.

        Returns ``True`` if the agent is (now) connected, ``False`` if the
        wait expired. Used to absorb brief network drops where the caller
        is willing to retry-after-reconnect rather than failing immediately.

        Implemented as a loop that re-arms the wake event after every
        spurious wake-up: if an agent registers and immediately
        disconnects before we observed the new state, we keep waiting
        up to the remaining timeout instead of returning ``False``
        prematurely.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout > 0 else loop.time()

        if self.is_connected(agent_id):
            return True
        if timeout <= 0:
            return False

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return self.is_connected(agent_id)
            event = asyncio.Event()
            self._connect_waiters.setdefault(agent_id, set()).add(event)
            try:
                try:
                    await asyncio.wait_for(event.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return self.is_connected(agent_id)
            finally:
                waiters = self._connect_waiters.get(agent_id)
                if waiters:
                    waiters.discard(event)
                    if not waiters:
                        self._connect_waiters.pop(agent_id, None)
            if self.is_connected(agent_id):
                return True
            # Spurious wake (e.g. race between register and disconnect);
            # loop and re-arm a fresh event for the remaining time.

    # ── Outbound messaging ──────────────────────────────────────

    async def send_raw(self, agent_id: str, payload: dict[str, Any]) -> None:
        """Send a fire-and-forget JSON payload to an agent (no response awaited).

        Use this for one-way messages like chat dispatch / cancel where the
        response arrives asynchronously through other channels (e.g. chat events).
        Raises AgentOfflineError if the agent is not connected.

        The per-agent send lock is held across ``ws.send_text`` so
        this cannot interleave with a concurrent ``send_request`` and
        produce a corrupt frame stream.
        """
        ws = self._connections.get(agent_id)
        if not ws:
            raise AgentOfflineError(agent_id)
        async with self._get_send_lock(agent_id):
            # Re-fetch under the lock: a reconnect might have replaced
            # ws while we were waiting. We always want to send on the
            # currently-registered connection.
            ws = self._connections.get(agent_id)
            if not ws:
                raise AgentOfflineError(agent_id)
            await ws.send_text(json.dumps(payload))

    async def send_request(
        self,
        agent_id: str,
        msg_type: str,
        payload: dict,
        timeout: float = 60.0,
        wait_for_agent: float = 0.0,
    ) -> dict:
        """Send request to agent and await response via Future.

        Wire format (introduced 2026-04-08 with the envelope redesign):

            outbound:  {"type": <msg_type>, "request_id": <hex>,
                        "payload": <payload>}
            inbound:   {"type": <msg_type>_result, "request_id": <hex>,
                        "payload": <inner-result>}

        Envelope keys (``type``, ``request_id``) live at the top level
        and are owned by the dispatcher. All caller / handler data lives
        nested under ``payload`` so it cannot collide with envelope keys.
        This method unwraps ``payload`` before returning, so MCP tools
        continue to see a flat dict — only the wire-level shape changed.

        ``wait_for_agent``: when > 0, block up to that many seconds waiting
        for the agent to (re)connect before failing with AgentOfflineError.
        Useful for absorbing brief disconnects (sleep/wake, WiFi switch).

        Back-pressure: rejects with :class:`AgentBusyError` if either
        the per-agent pending count exceeds ``MAX_PENDING_PER_AGENT``
        or the global pending count exceeds ``MAX_PENDING_GLOBAL``.
        Caps include waiting callers (semaphore queue), not just
        Future-holders, so a runaway producer cannot starve the
        manager into unbounded memory growth.
        """
        # Back-pressure admission control BEFORE any waiting. This
        # keeps failures fast: callers see the rejection right away
        # instead of hanging inside wait_for_connection.
        per_agent = self._pending_count.get(agent_id, 0)
        global_pending = self._global_pending()
        if per_agent >= MAX_PENDING_PER_AGENT or global_pending >= MAX_PENDING_GLOBAL:
            raise AgentBusyError(
                agent_id,
                per_agent=per_agent,
                global_pending=global_pending,
            )

        ws = self._connections.get(agent_id)
        if not ws and wait_for_agent > 0:
            if await self.wait_for_connection(agent_id, wait_for_agent):
                ws = self._connections.get(agent_id)
        if not ws:
            raise AgentOfflineError(agent_id)

        # Reserve a pending slot for the whole semaphore-wait + send +
        # response lifecycle. This is accounted as pending from the
        # caller's perspective even while we block on the semaphore,
        # which is what back-pressure admission needs to prevent an
        # unbounded queue.
        self._increment_pending(agent_id)
        try:
            sem = self._get_inflight_semaphore(agent_id)
            async with sem:
                # Re-check connection after semaphore wait — a long
                # queue could have lasted past a disconnect.
                ws = self._connections.get(agent_id)
                if not ws:
                    raise AgentOfflineError(agent_id)

                # Full 128-bit UUID. The WebSocket dispatcher routes
                # responses purely by request_id, so a predictable or
                # collision-prone id would let one in-flight request's
                # Future be resolved by another's response. Do not
                # shorten this.
                request_id = uuid.uuid4().hex
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._pending[request_id] = (agent_id, future)

                try:
                    async with self._get_send_lock(agent_id):
                        # Re-fetch ws under the send lock so a concurrent
                        # reconnect cannot cause us to write into a closed
                        # socket.
                        ws = self._connections.get(agent_id)
                        if not ws:
                            raise AgentOfflineError(agent_id)
                        await ws.send_text(json.dumps({
                            "type": msg_type,
                            "request_id": request_id,
                            "payload": payload,
                        }))
                    envelope = await asyncio.wait_for(future, timeout=timeout)
                    if not isinstance(envelope, dict):
                        raise RuntimeError(
                            f"Agent response is not a dict: {type(envelope).__name__}"
                        )
                    inner = envelope.get("payload")
                    if not isinstance(inner, dict):
                        # Loud failure: the agent broke the wire contract. Do not
                        # silently substitute an empty dict — surface the bug so
                        # operators can see exactly which handler regressed.
                        raise RuntimeError(
                            f"Agent response missing ``payload`` dict "
                            f"(envelope keys: {sorted(envelope.keys())})"
                        )
                    if inner.get("error"):
                        raise RuntimeError(inner["error"])
                    return inner
                except asyncio.TimeoutError:
                    raise CommandTimeoutError(request_id, timeout)
                finally:
                    self._pending.pop(request_id, None)
        finally:
            self._decrement_pending(agent_id)

    def resolve_request(self, msg: dict) -> bool:
        """Resolve a pending request with an incoming response message."""
        request_id = msg.get("request_id")
        if not request_id:
            return False
        entry = self._pending.get(request_id)
        if entry and not entry[1].done():
            entry[1].set_result(msg)
            return True
        return False


# Module-level singleton — import this from anywhere instead of constructing
# new instances. Tests can swap it out via monkeypatch on this module.
agent_manager = AgentConnectionManager()
