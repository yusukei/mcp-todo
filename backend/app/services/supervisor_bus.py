"""Redis pub/sub routing for the multi-worker supervisor transport.

Mirrors :mod:`agent_bus` but for the simpler 1:1 supervisor surface.
Each supervisor WS is owned by exactly one uvicorn worker (the one
that accepted the auth handshake). MCP HTTP requests can land on
any worker, so the bus routes RPCs through Redis to the owning
worker.

## Key layout (DB 0)

================================ ============== =========================================
Key                              Type           Purpose
================================ ============== =========================================
``supervisor:registry:{sid}``    STRING (TTL)   ``worker_id`` of the owning worker.
                                                  Refreshed by the WS handler each time
                                                  the supervisor sends a frame
                                                  (heartbeat or push).
``supervisor:cmd:{worker_id}``   LIST           Command envelopes (BLPOP-drained by the
                                                  owning worker's command listener).
``supervisor:resp:{rid}``        pub/sub        One-shot response channel.
``supervisor:events``            pub/sub        Connect / disconnect broadcasts.
================================ ============== =========================================

## Differences from ``agent_bus``

The supervisor RPC surface is small (status / restart / logs /
upgrade / config_reload) and has no per-supervisor concurrency
caps; the bus does not encode busy / shutting_down distinctions.
There's also no ``wait_for_connection`` — operators always
target an existing supervisor by id, never wait for one to come
online — so the events listener only logs.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


# Mirrors agent_bus's TTL: 3× the supervisor's heartbeat interval
# (default 30 s on the supervisor side) so a single missed
# heartbeat does not flip the supervisor offline.
REGISTRY_TTL_SECONDS = 90
COMMAND_BLPOP_TIMEOUT_SECONDS = 1

KEY_REGISTRY = "supervisor:registry:{supervisor_id}"
KEY_CMD_QUEUE = "supervisor:cmd:{worker_id}"
CHANNEL_RESPONSE = "supervisor:resp:{request_id}"
CHANNEL_EVENTS = "supervisor:events"


class SupervisorBusOffline(Exception):
    """No worker owns this supervisor (registry empty)."""


class SupervisorBusTimeout(Exception):
    """The owning worker did not publish a response in time."""

    def __init__(self, request_id: str, timeout: float) -> None:
        self.request_id = request_id
        self.timeout = timeout
        super().__init__(
            f"supervisor bus request {request_id} timed out after {timeout}s"
        )


async def _del_if_owner(
    redis: "aioredis.Redis",
    key: str,
    expected_owner: str,
) -> bool:
    """Atomically DEL ``key`` only if its current value equals
    ``expected_owner`` (compare-and-delete via WATCH/MULTI/EXEC)."""
    async with redis.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(key)
            current = await pipe.get(key)
            if isinstance(current, bytes):
                current = current.decode("utf-8")
            if current != expected_owner:
                await pipe.unwatch()
                return False
            pipe.multi()
            pipe.delete(key)
            await pipe.execute()
            return True
        except Exception:
            return False


class RedisSupervisorBus:
    """Redis-backed routing layer for cross-worker supervisor RPCs.

    Owned by :class:`SupervisorConnectionManager` alongside the local
    in-process registry. The bus does not touch Redis at construction
    time — :meth:`start` wires up the listener tasks.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        local: "SupervisorConnectionManager",  # forward ref
        redis_client: "aioredis.Redis | None" = None,
    ) -> None:
        self.worker_id = worker_id
        self._local = local
        self._redis: "aioredis.Redis | None" = redis_client
        self._command_task: asyncio.Task | None = None
        self._events_task: asyncio.Task | None = None
        self._stopping = False
        self._owned: set[str] = set()
        # Strong refs for in-flight dispatches; CPython only retains
        # weak references to bare ``create_task`` results.
        self._dispatch_tasks: set[asyncio.Task] = set()

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        if self._command_task is not None:
            return
        if self._redis is None:
            from ..core.redis import get_redis
            self._redis = get_redis()
        self._stopping = False
        self._command_task = asyncio.create_task(
            self._command_listener(),
            name=f"supervisor-bus-cmd-{self.worker_id[:8]}",
        )
        self._events_task = asyncio.create_task(
            self._events_listener(),
            name=f"supervisor-bus-events-{self.worker_id[:8]}",
        )
        logger.info(
            "RedisSupervisorBus started (worker_id=%s)", self.worker_id
        )

    async def stop(self) -> None:
        if self._command_task is None:
            return
        self._stopping = True
        if self._dispatch_tasks:
            await asyncio.gather(
                *self._dispatch_tasks, return_exceptions=True,
            )
        for task in (self._command_task, self._events_task):
            if task is not None:
                task.cancel()
        for task in (self._command_task, self._events_task):
            if task is None:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "RedisSupervisorBus background task raised during stop"
                )
        self._command_task = None
        self._events_task = None
        if self._redis is not None and self._owned:
            for sid in list(self._owned):
                with contextlib.suppress(Exception):
                    await _del_if_owner(
                        self._redis,
                        KEY_REGISTRY.format(supervisor_id=sid),
                        self.worker_id,
                    )
                    await self._redis.publish(
                        CHANNEL_EVENTS,
                        json.dumps({
                            "type": "disconnect",
                            "supervisor_id": sid,
                            "worker_id": self.worker_id,
                        }),
                    )
        logger.info(
            "RedisSupervisorBus stopped (worker_id=%s)", self.worker_id
        )

    # ── Local register / unregister hooks ───────────────────────

    async def on_local_register(self, supervisor_id: str) -> None:
        self._owned.add(supervisor_id)
        if self._redis is None:
            return
        try:
            await self._redis.set(
                KEY_REGISTRY.format(supervisor_id=supervisor_id),
                self.worker_id,
                ex=REGISTRY_TTL_SECONDS,
            )
            await self._redis.publish(
                CHANNEL_EVENTS,
                json.dumps({
                    "type": "connect",
                    "supervisor_id": supervisor_id,
                    "worker_id": self.worker_id,
                }),
            )
        except Exception:
            logger.exception(
                "Failed to publish bus connect for supervisor %s",
                supervisor_id,
            )
            raise

    async def on_local_unregister(self, supervisor_id: str) -> None:
        self._owned.discard(supervisor_id)
        if self._redis is None:
            return
        try:
            await _del_if_owner(
                self._redis,
                KEY_REGISTRY.format(supervisor_id=supervisor_id),
                self.worker_id,
            )
            await self._redis.publish(
                CHANNEL_EVENTS,
                json.dumps({
                    "type": "disconnect",
                    "supervisor_id": supervisor_id,
                    "worker_id": self.worker_id,
                }),
            )
        except Exception:
            logger.exception(
                "Failed to publish bus disconnect for supervisor %s",
                supervisor_id,
            )
            raise

    async def refresh_registration(self, supervisor_id: str) -> None:
        """Re-extend the registry TTL for a locally-owned supervisor.

        Called by the WS handler each time a frame arrives so the
        90 s TTL never expires on a healthy connection. Idempotent.
        """
        if self._redis is None or supervisor_id not in self._owned:
            return
        key = KEY_REGISTRY.format(supervisor_id=supervisor_id)
        try:
            owner = await self._redis.get(key)
            if isinstance(owner, bytes):
                owner = owner.decode("utf-8")
            if owner == self.worker_id:
                await self._redis.expire(key, REGISTRY_TTL_SECONDS)
        except Exception:
            logger.exception(
                "refresh_registration failed for supervisor %s",
                supervisor_id,
            )

    # ── Owner discovery ─────────────────────────────────────────

    async def get_owner(self, supervisor_id: str) -> str | None:
        if self._redis is None:
            return None
        owner = await self._redis.get(
            KEY_REGISTRY.format(supervisor_id=supervisor_id)
        )
        if isinstance(owner, bytes):
            owner = owner.decode("utf-8")
        return owner

    async def is_remotely_connected(self, supervisor_id: str) -> bool:
        if self._local.is_connected(supervisor_id):
            return True
        owner = await self.get_owner(supervisor_id)
        return owner is not None

    # ── Outbound: forward an RPC to the owning worker ───────────

    async def send_request_remote(
        self,
        supervisor_id: str,
        msg_type: str,
        payload: dict,
        timeout: float,
    ) -> dict:
        """Push an RPC envelope onto the owner's command queue and
        await its response on a one-shot pub/sub channel.

        Caller is responsible for first checking that the supervisor
        is NOT locally owned. Raises :class:`SupervisorBusOffline`
        if the registry is empty, :class:`SupervisorBusTimeout` if
        the owner does not publish in time. Other RPC-level errors
        propagate as the response payload's ``error`` field.
        """
        if self._redis is None:
            raise SupervisorBusOffline(supervisor_id)
        owner = await self.get_owner(supervisor_id)
        if not owner:
            raise SupervisorBusOffline(supervisor_id)

        bus_request_id = uuid.uuid4().hex
        response_channel = CHANNEL_RESPONSE.format(request_id=bus_request_id)
        pubsub = self._redis.pubsub()
        # Subscribe BEFORE rpush — Redis pub/sub does not buffer.
        await pubsub.subscribe(response_channel)
        try:
            envelope = {
                "request_id": bus_request_id,
                "supervisor_id": supervisor_id,
                "msg_type": msg_type,
                "payload": payload,
                "timeout": timeout,
                "origin_worker_id": self.worker_id,
            }
            await self._redis.rpush(
                KEY_CMD_QUEUE.format(worker_id=owner),
                json.dumps(envelope),
            )

            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SupervisorBusTimeout(bus_request_id, timeout)
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=min(remaining, 1.0),
                    )
                except asyncio.TimeoutError:
                    msg = None
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                response = json.loads(data)
                kind = response.get("error_kind")
                inner = response.get("payload") or {}
                if kind == "offline":
                    raise SupervisorBusOffline(supervisor_id)
                if kind == "timeout":
                    raise SupervisorBusTimeout(
                        bus_request_id, response.get("timeout", timeout)
                    )
                if kind:
                    # Unknown / generic error: surface as a payload
                    # error so the MCP layer can wrap it in ToolError.
                    return {
                        "__type__": response.get("type"),
                        "error": inner.get("error", f"unknown bus error_kind={kind}"),
                    }
                return inner
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(response_channel)
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    # ── Inbound: receive a remote request and dispatch locally ──

    async def _command_listener(self) -> None:
        """Drain ``supervisor:cmd:{my_worker_id}`` and dispatch."""
        assert self._redis is not None
        key = KEY_CMD_QUEUE.format(worker_id=self.worker_id)
        try:
            await self._redis.delete(key)
        except Exception:
            logger.exception("Failed to clear stale command queue %s", key)

        while not self._stopping:
            try:
                result = await self._redis.blpop(
                    [key], timeout=COMMAND_BLPOP_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "supervisor command listener BLPOP failed; backing off"
                )
                await asyncio.sleep(0.5)
                continue
            if result is None:
                continue
            _, raw = result
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                logger.exception(
                    "Dropping malformed supervisor command envelope: %s",
                    raw[:200],
                )
                continue
            task = asyncio.create_task(
                self._dispatch_remote(envelope),
                name=f"supervisor-bus-dispatch-{envelope.get('request_id', '?')[:8]}",
            )
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch_remote(self, envelope: dict[str, Any]) -> None:
        """Run a remote-originated command on our local registry."""
        assert self._redis is not None
        bus_request_id = envelope.get("request_id", "")
        supervisor_id = envelope.get("supervisor_id", "")
        msg_type = envelope.get("msg_type", "")
        payload = envelope.get("payload") or {}
        timeout = float(envelope.get("timeout", 60.0))
        response_channel = CHANNEL_RESPONSE.format(request_id=bus_request_id)

        if not bus_request_id:
            logger.error(
                "Dropping bus envelope with no request_id "
                "(supervisor_id=%s, msg_type=%s)",
                supervisor_id, msg_type,
            )
            return

        response: dict[str, Any]
        try:
            result = await self._local.send_request_local(
                supervisor_id, msg_type, payload, timeout=timeout,
            )
            response = {"payload": result}
        except _LocalOfflineError:
            response = {
                "payload": {"error": f"Supervisor {supervisor_id} is offline"},
                "error_kind": "offline",
            }
        except _LocalTimeoutError as e:
            response = {
                "payload": {"error": str(e)},
                "error_kind": "timeout",
                "timeout": e.timeout,
            }
        except Exception as e:
            logger.exception(
                "Unhandled error dispatching remote supervisor command "
                "(request_id=%s, supervisor_id=%s, msg_type=%s)",
                bus_request_id, supervisor_id, msg_type,
            )
            response = {
                "payload": {"error": f"{type(e).__name__}: {e}"},
                "error_kind": "runtime",
            }

        try:
            await self._redis.publish(response_channel, json.dumps(response))
        except Exception:
            logger.exception(
                "Failed to publish supervisor bus response on %s",
                response_channel,
            )

    async def _events_listener(self) -> None:
        """Tail ``supervisor:events`` for log + future hook points."""
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL_EVENTS)
        try:
            while not self._stopping:
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    msg = None
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                # Parse + log only — no waiters today. Future hook
                # point if we ever add a wait_for_supervisor_connect.
                with contextlib.suppress(json.JSONDecodeError):
                    event = json.loads(data)
                    if event.get("worker_id") != self.worker_id:
                        logger.debug(
                            "supervisor bus event: %s", event,
                        )
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(CHANNEL_EVENTS)
            with contextlib.suppress(Exception):
                await pubsub.aclose()


# Re-imported by the manager so the bus's dispatch path can catch
# the manager's local exception types without circular imports.
class _LocalOfflineError(Exception):
    """Internal signal — the local supervisor disappeared mid-RPC."""


class _LocalTimeoutError(Exception):
    """Internal signal — the local RPC ran out of time."""

    def __init__(self, request_id: str, timeout: float) -> None:
        self.request_id = request_id
        self.timeout = timeout
        super().__init__(
            f"local RPC {request_id} timed out after {timeout}s"
        )
