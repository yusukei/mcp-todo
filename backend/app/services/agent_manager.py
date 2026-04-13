"""Agent connection manager facade.

The actual in-process state machine lives in
:mod:`agent_local_transport` and the cross-worker routing layer in
:mod:`agent_bus`. This module composes them through
``AgentConnectionManager`` and exports the public symbols
(exception classes, back-pressure constants, the ``agent_manager``
singleton) so existing callers do not have to change after the
P1 multi-worker switch.

## Routing model

A request for ``agent_id`` reaches the manager's ``send_request``.
The manager picks the route up-front based on **ownership**, not as
a fallback (per CLAUDE.md "no silent fallbacks"):

- If the local :class:`LocalAgentTransport` already holds the
  WebSocket for ``agent_id`` → run the local path. No Redis hop,
  no latency tax.
- Otherwise → forward through :class:`RedisAgentBus` to whichever
  worker owns the WebSocket.

Routing is deterministic: the same ``agent_id`` resolves to the
same path on every call until ``register`` / ``unregister`` flips
the local ownership.

## Why the constants live here and not in agent_local_transport

``LocalAgentTransport`` reads ``MAX_PENDING_PER_AGENT`` /
``MAX_PENDING_GLOBAL`` / ``MAX_INFLIGHT_PER_AGENT`` via late binding
through this module (``from . import agent_manager as _am``). Tests
monkeypatch these names on this module, and the late lookup means
the override takes effect on the very next call without anyone
having to re-import or rebuild semaphores. Moving the constants
into ``agent_local_transport`` would silently break those tests
because ``monkeypatch.setattr`` only rebinds the name in the
module it is given.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from .agent_bus import RedisAgentBus
from .agent_local_transport import (
    AgentBusyError,
    AgentOfflineError,
    AgentShuttingDownError,
    CommandTimeoutError,
    LocalAgentTransport,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from fastapi import WebSocket

__all__ = [
    "AgentBusyError",
    "AgentOfflineError",
    "AgentShuttingDownError",
    "CommandTimeoutError",
    "AgentConnectionManager",
    "agent_manager",
    "MAX_INFLIGHT_PER_AGENT",
    "MAX_PENDING_PER_AGENT",
    "MAX_PENDING_GLOBAL",
]


# Per-agent concurrency caps. These are intentionally module-level
# constants rather than Settings so tests can patch them without
# pytest-env gymnastics. The defaults are sized for a single-process
# deployment talking to a handful of agents: each agent can run 8
# concurrent operations, with up to 64 requests queued on top of
# that. Global ceiling caps the worst case across all agents.
MAX_INFLIGHT_PER_AGENT = 8
MAX_PENDING_PER_AGENT = 64
MAX_PENDING_GLOBAL = 512


class AgentConnectionManager:
    """Public facade composing the local transport and the Redis bus.

    The facade replaces the historical "thin subclass of
    LocalAgentTransport" so PR 2 can introduce the routing layer
    without changing the call sites in ``mcp/tools/remote.py``,
    ``api/v1/endpoints/workspaces/`` and ``services/chat_events.py``.

    Construction does NOT touch Redis. The bus is wired up only when
    :meth:`start` is called from the application lifespan, so unit
    tests that operate on locally-owned agents can ``register`` and
    ``send_request`` without any Redis client.
    """

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        self.worker_id = worker_id or uuid.uuid4().hex
        self._local = LocalAgentTransport()
        self._bus = RedisAgentBus(
            worker_id=self.worker_id,
            local=self._local,
            redis_client=redis_client,
        )

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Wire the bus to Redis. Idempotent."""
        await self._bus.start()

    async def stop(self) -> None:
        """Stop bus listeners (does NOT touch the local transport)."""
        await self._bus.stop()

    def start_shutdown(self) -> None:
        """Stop accepting new agent requests at the local layer."""
        self._local.start_shutdown()

    async def drain(self, timeout: float) -> bool:
        """Wait for in-flight local RPCs to finish, up to ``timeout`` seconds."""
        return await self._local.drain(timeout)

    # ── Connection lifecycle ────────────────────────────────────

    async def register(self, agent_id: str, ws: WebSocket) -> None:
        """Register a local WebSocket and broadcast the connect to the cluster."""
        await self._local.register(agent_id, ws)
        await self._bus.on_local_register(agent_id)

    async def unregister(
        self, agent_id: str, ws: WebSocket | None = None,
    ) -> None:
        """Unregister a local WebSocket and broadcast the disconnect."""
        was_connected = self._local.is_connected(agent_id)
        await self._local.unregister(agent_id, ws)
        # Only broadcast if the local transport actually let go of
        # the connection (the stale-handler unregister is a no-op
        # and must NOT remove the registry entry that the live
        # handler claimed).
        if was_connected and not self._local.is_connected(agent_id):
            await self._bus.on_local_unregister(agent_id)

    def is_connected(self, agent_id: str) -> bool:
        """Local-only check.

        Kept synchronous to preserve the existing call surface.
        Callers that need a cluster-wide answer should use
        :meth:`is_connected_anywhere`.
        """
        return self._local.is_connected(agent_id)

    async def is_connected_anywhere(self, agent_id: str) -> bool:
        """Cluster-wide connectivity check (consults the Redis registry)."""
        return await self._bus.is_remotely_connected(agent_id)

    def get_connected_agent_ids(self) -> list[str]:
        """Local-only list. Use :meth:`get_all_agent_ids` for the cluster view."""
        return self._local.get_connected_agent_ids()

    async def get_all_agent_ids(self) -> list[str]:
        """Cluster-wide agent id list (consults the Redis registry)."""
        return await self._bus.list_remote_agent_ids()

    async def wait_for_connection(self, agent_id: str, timeout: float) -> bool:
        """Block until ``agent_id`` connects, locally or anywhere in the cluster.

        Waits on the local transport's per-agent connect waiter set.
        Local registers wake the waiter through
        :meth:`LocalAgentTransport.register`. Remote registers from
        another worker wake the same waiter through the bus events
        listener (see :meth:`agent_bus.RedisAgentBus._events_listener`),
        so a single wait covers both cases.
        """
        if self._local.is_connected(agent_id):
            return True
        if await self._bus.is_remotely_connected(agent_id):
            return True
        if timeout <= 0:
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Reuse the LocalAgentTransport's per-agent connect waiter
        # mechanism so registers from this worker AND remote-connect
        # broadcasts forwarded by the bus both wake the same event.
        # Public ``add_connect_waiter`` / ``discard_connect_waiter``
        # avoid touching the private ``_connect_waiters`` dict from
        # outside the LocalAgentTransport class.
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return self._local.is_connected(agent_id) or await self._bus.is_remotely_connected(agent_id)
            event = asyncio.Event()
            self._local.add_connect_waiter(agent_id, event)
            try:
                try:
                    await asyncio.wait_for(event.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return self._local.is_connected(agent_id) or await self._bus.is_remotely_connected(agent_id)
            finally:
                self._local.discard_connect_waiter(agent_id, event)
            if self._local.is_connected(agent_id):
                return True
            if await self._bus.is_remotely_connected(agent_id):
                return True
            # Spurious wake — re-arm for the remaining time.

    # ── Messaging ───────────────────────────────────────────────

    async def send_raw(self, agent_id: str, payload: dict[str, Any]) -> None:
        """Send a fire-and-forget message.

        Same routing model as :meth:`send_request`. For remote
        agents the bus uses a synthetic ``__send_raw__`` msg_type
        that the receiving worker translates back into a local
        ``send_raw``.
        """
        if self._local.is_connected(agent_id):
            await self._local.send_raw(agent_id, payload)
            return
        # Remote: route via the bus. We piggy-back on send_request
        # but ignore the response — best-effort, matching the
        # local send_raw semantics.
        owner = await self._bus.get_owner(agent_id)
        if owner is None:
            raise AgentOfflineError(agent_id)
        # The bus uses send_request_remote which expects a
        # request/response pair. For send_raw we synthesise a
        # short-timeout ack: the receiving worker treats
        # __send_raw__ as a fire-and-forget locally and returns
        # an empty payload.
        await self._bus.send_request_remote(
            agent_id,
            "__send_raw__",
            {"payload": payload},
            timeout=10.0,
        )

    async def send_request(
        self,
        agent_id: str,
        msg_type: str,
        payload: dict,
        timeout: float = 60.0,
        wait_for_agent: float = 0.0,
    ) -> dict:
        """Route a request to the worker that owns ``agent_id``."""
        if self._local.is_connected(agent_id):
            return await self._local.send_request(
                agent_id, msg_type, payload,
                timeout=timeout, wait_for_agent=wait_for_agent,
            )

        # Not local. If the caller is willing to wait, block on the
        # cluster-wide connect broadcast and re-check before going
        # straight to AgentOfflineError.
        if wait_for_agent > 0 and await self.wait_for_connection(
            agent_id, wait_for_agent,
        ):
            if self._local.is_connected(agent_id):
                return await self._local.send_request(
                    agent_id, msg_type, payload, timeout=timeout,
                )

        return await self._bus.send_request_remote(
            agent_id, msg_type, payload, timeout=timeout,
        )

    async def refresh_agent_registration(self, agent_id: str) -> None:
        """Reset the Redis TTL for ``agent_id`` on receiving a ping frame.

        Delegates to the bus so only the owning worker refreshes the key.
        No-op when Redis is unavailable or the agent is not locally owned.
        """
        await self._bus.refresh_agent_registration(agent_id)

    def resolve_request(self, msg: dict) -> bool:
        """Local-only future correlation hook for the WS receive loop."""
        return self._local.resolve_request(msg)


# Module-level singleton — import this from anywhere instead of
# constructing new instances. Tests can swap it out via monkeypatch
# on this module.
agent_manager = AgentConnectionManager()
