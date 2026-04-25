"""Multi-worker-aware supervisor connection manager.

Composes a local in-process registry (one ``WebSocket`` per
``RemoteSupervisor`` per worker) with :class:`RedisSupervisorBus`
so RPCs from MCP tools transparently route to whichever uvicorn
worker actually owns the supervisor's WebSocket — even when
``WEB_CONCURRENCY > 1``.

## Public surface (kept stable for ``handlers``, MCP tools, tests)

- :meth:`register` / :meth:`unregister` — call from the WS handler.
- :meth:`send_request` — RPC entry point (handles local/remote routing).
- :meth:`resolve_request` — sync hook for the WS receive loop to
  correlate inbound RPC responses to pending Futures.
- :meth:`is_connected` — local-only check (cheap, sync).
- :meth:`is_connected_anywhere` — cluster-wide check via the bus.

## Routing rules

The manager checks local ownership first. If the supervisor is
locally connected, the in-process Future-based path serves the
RPC (zero latency tax). Otherwise the bus forwards the RPC to
the owning worker over Redis, and the response comes back via a
one-shot pub/sub channel.

Tests using only locally-owned supervisors do not need a Redis
client — the bus's ``start()`` is wired up from the FastAPI
lifespan, not from the manager constructor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from .supervisor_bus import (
    RedisSupervisorBus,
    SupervisorBusOffline,
    SupervisorBusTimeout,
    _LocalOfflineError,
    _LocalTimeoutError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_S = 60.0


class SupervisorOfflineError(Exception):
    """Raised when an RPC targets a supervisor that isn't connected
    locally or in any other worker (registry empty)."""


class SupervisorRpcTimeout(Exception):
    """Raised when an RPC response doesn't arrive in time."""


class SupervisorConnectionManager:
    def __init__(
        self,
        *,
        worker_id: str | None = None,
        redis_client: "aioredis.Redis | None" = None,
    ) -> None:
        self.worker_id = worker_id or uuid.uuid4().hex
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # request_id -> supervisor_id, so a per-supervisor disconnect
        # only fails its own pending RPCs.
        self._pending_owner: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._bus = RedisSupervisorBus(
            worker_id=self.worker_id,
            local=self,
            redis_client=redis_client,
        )

    # ── Lifecycle (called from FastAPI lifespan) ────────────────

    async def start(self) -> None:
        """Wire the bus to Redis. Idempotent."""
        await self._bus.start()

    async def stop(self) -> None:
        """Stop bus listeners + drop the registry entries we own."""
        await self._bus.stop()

    # ── Connection lifecycle ─────────────────────────────────────

    async def register(self, supervisor_id: str, ws: WebSocket) -> None:
        """Claim ownership locally + broadcast via the bus."""
        async with self._lock:
            existing = self._connections.get(supervisor_id)
            if existing is not None and existing is not ws:
                logger.info(
                    "supervisor_manager: replacing stale ws for supervisor=%s",
                    supervisor_id,
                )
            self._connections[supervisor_id] = ws
        # Best-effort bus broadcast. If Redis is down (e.g. during
        # tests with no bus.start()), on_local_register is a no-op.
        try:
            await self._bus.on_local_register(supervisor_id)
        except Exception:
            logger.exception(
                "bus on_local_register failed for supervisor=%s; "
                "local routing still works",
                supervisor_id,
            )

    async def unregister(
        self,
        supervisor_id: str,
        ws: WebSocket | None = None,
    ) -> None:
        """Release ownership + cancel pending RPCs scoped to this
        supervisor + broadcast disconnect via the bus."""
        to_cancel: list[str] = []
        evicted = False
        async with self._lock:
            current = self._connections.get(supervisor_id)
            if current is None:
                return
            if ws is not None and current is not ws:
                # Stale: a fresher reconnect already claimed the slot.
                return
            self._connections.pop(supervisor_id, None)
            evicted = True
            for rid, owner in list(self._pending_owner.items()):
                if owner == supervisor_id:
                    to_cancel.append(rid)
                    self._pending_owner.pop(rid, None)
        for rid in to_cancel:
            fut = self._pending.pop(rid, None)
            if fut and not fut.done():
                fut.set_exception(SupervisorOfflineError(supervisor_id))
        if evicted:
            try:
                await self._bus.on_local_unregister(supervisor_id)
            except Exception:
                logger.exception(
                    "bus on_local_unregister failed for supervisor=%s",
                    supervisor_id,
                )

    async def refresh_registration(self, supervisor_id: str) -> None:
        """Extend the registry TTL on inbound supervisor frames."""
        await self._bus.refresh_registration(supervisor_id)

    def is_connected(self, supervisor_id: str) -> bool:
        return supervisor_id in self._connections

    async def is_connected_anywhere(self, supervisor_id: str) -> bool:
        return await self._bus.is_remotely_connected(supervisor_id)

    def get_connected_supervisor_ids(self) -> list[str]:
        return list(self._connections.keys())

    # ── Messaging ────────────────────────────────────────────────

    async def send_request(
        self,
        supervisor_id: str,
        msg_type: str,
        payload: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Route an RPC to whichever worker owns the supervisor.

        Local supervisors take the in-process Future path. Remote
        supervisors go through the Redis bus. The dispatch decision
        is up-front — if we own the WS we ARE the only path that can
        serve it; if we don't, the registry is the source of truth.
        """
        if self.is_connected(supervisor_id):
            try:
                return await self.send_request_local(
                    supervisor_id, msg_type, payload, timeout=timeout,
                )
            except _LocalOfflineError as e:
                raise SupervisorOfflineError(supervisor_id) from e
            except _LocalTimeoutError as e:
                raise SupervisorRpcTimeout(str(e)) from e

        # Not local. Forward via the bus.
        try:
            return await self._bus.send_request_remote(
                supervisor_id,
                msg_type,
                payload or {},
                timeout=timeout,
            )
        except SupervisorBusOffline as e:
            raise SupervisorOfflineError(supervisor_id) from e
        except SupervisorBusTimeout as e:
            raise SupervisorRpcTimeout(str(e)) from e

    async def send_request_local(
        self,
        supervisor_id: str,
        msg_type: str,
        payload: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """In-process RPC path: send the envelope on our local WS
        and await the response Future.

        Raises the bus's internal :class:`_LocalOfflineError` /
        :class:`_LocalTimeoutError` so :meth:`RedisSupervisorBus.
        _dispatch_remote` can translate them into bus envelope
        ``error_kind`` markers without a circular import.
        """
        ws = self._connections.get(supervisor_id)
        if ws is None:
            raise _LocalOfflineError(supervisor_id)

        request_id = secrets.token_hex(8)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()

        async with self._lock:
            self._pending[request_id] = fut
            self._pending_owner[request_id] = supervisor_id

        envelope = {
            "type": msg_type,
            "request_id": request_id,
            "payload": payload or {},
        }
        try:
            await ws.send_text(json.dumps(envelope))
        except Exception as e:
            async with self._lock:
                self._pending.pop(request_id, None)
                self._pending_owner.pop(request_id, None)
            raise _LocalOfflineError(
                f"send to supervisor={supervisor_id} failed: {e}"
            ) from e

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as e:
            async with self._lock:
                self._pending.pop(request_id, None)
                self._pending_owner.pop(request_id, None)
            raise _LocalTimeoutError(request_id, timeout) from e
        except SupervisorOfflineError:
            # Disconnect cancelled the future while we awaited.
            raise _LocalOfflineError(supervisor_id) from None

    def resolve_request(self, msg: dict[str, Any]) -> bool:
        """Correlate an inbound frame to a pending RPC by ``request_id``."""
        rid = msg.get("request_id")
        if not rid:
            return False
        fut = self._pending.pop(rid, None)
        self._pending_owner.pop(rid, None)
        if fut is None:
            return False
        if not fut.done():
            payload = msg.get("payload") or {}
            if isinstance(payload, dict):
                payload["__type__"] = msg.get("type")
            fut.set_result(payload)
        return True


# Module-level singleton. Tests that need isolation can monkeypatch
# this with a fresh ``SupervisorConnectionManager()`` instance; the
# bus's ``start()`` is opt-in so tests don't need a Redis client.
supervisor_manager = SupervisorConnectionManager()
