"""Redis pub/sub routing for the multi-worker agent transport.

When the backend runs with ``uvicorn workers > 1`` (or several
container replicas), each agent WebSocket is owned by exactly one
worker — the one that accepted the agent's connect handshake. MCP
HTTP requests, however, can land on any worker. The
:class:`RedisAgentBus` bridges the gap by routing requests through
Redis to the worker that actually holds the WebSocket.

## Key layout (DB 0)

================================ ============== =========================================
Key                              Type           Purpose
================================ ============== =========================================
``agent:registry:{agent_id}``    STRING (TTL 30s) ``worker_id`` of the owning worker.
                                                  Refreshed every 10s by
                                                  :meth:`_heartbeat_loop` so a crashed
                                                  owner's entry expires within ~30s.
``agent:cmd:{worker_id}``        LIST           Command envelopes (BLPOP-drained by the
                                                  owning worker's command listener task).
``agent:resp:{request_id}``      pub/sub        One-shot response channel — sender
                                                  subscribes BEFORE pushing the command
                                                  so the publish cannot race past it.
``agent:events``                 pub/sub        Connect / disconnect broadcasts so all
                                                  workers can wake ``wait_for_connection``
                                                  and prune stale references.
================================ ============== =========================================

## Routing rules

Owning worker = "this process holds the agent WebSocket". Route
decision (made up-front, **not** as a fallback per CLAUDE.md):

- Agent owned locally → :class:`LocalAgentTransport` directly
  (no Redis hop, no latency tax).
- Agent not owned locally → look up the registry, push to the
  owner's command queue, await on the response pub/sub channel.

The local-fast-path is *correctness*, not optimisation: the local
worker has the WebSocket and there is no other worker that can
serve the request. Routing decisions are deterministic and tested.

## Race avoidance for response pub/sub

Redis pub/sub does **not** buffer messages. If the sender pushes
the command before subscribing to the response channel, a fast
owner can publish the response into a void. This module enforces
the order:

    1. ``await pubsub.subscribe("agent:resp:{rid}")``
    2. ``await redis.rpush("agent:cmd:{owner}", envelope)``
    3. ``await pubsub.get_message(...)``

so the subscribe is established before the command can possibly
reach the owner.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from .agent_local_transport import (
    AgentBusyError,
    AgentOfflineError,
    AgentShuttingDownError,
    CommandTimeoutError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from .agent_local_transport import LocalAgentTransport

logger = logging.getLogger(__name__)


# TTL constants. Kept module-level (not Settings) so tests can
# monkeypatch them without env gymnastics.
#
# REGISTRY_TTL_SECONDS is set to 3× the agent-side heartbeat interval
# (30 s) so one missed ping does not immediately flip the agent offline.
# The TTL is refreshed on every received ping in the WebSocket handler
# (via refresh_agent_registration), not by a background heartbeat loop
# in this process — that approach was removed because it required a
# separate task whose clock could drift out of sync with the agent.
REGISTRY_TTL_SECONDS = 90
COMMAND_BLPOP_TIMEOUT_SECONDS = 1  # short so stop() unblocks quickly

# Channel and key prefixes — kept as module constants so the
# accompanying tests and any future ops tooling read from a
# single source of truth.
KEY_REGISTRY = "agent:registry:{agent_id}"
KEY_CMD_QUEUE = "agent:cmd:{worker_id}"
CHANNEL_RESPONSE = "agent:resp:{request_id}"
CHANNEL_EVENTS = "agent:events"

async def _del_if_owner(
    redis: aioredis.Redis, key: str, expected_owner: str,
) -> bool:
    """Atomically DEL ``key`` if its current value equals ``expected_owner``.

    Plain GET + DEL would race against another worker grabbing
    ownership between the read and the delete. We use a
    ``WATCH`` / ``MULTI`` / ``EXEC`` transaction so the delete is
    only committed if no other client touched the key between the
    GET and the DEL. ``WATCH`` is portable across real Redis and
    fakeredis (Lua ``EVAL`` is not).
    """
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
            # WatchError or any other transaction failure means
            # someone else won the race — leave the registry as is.
            return False


class RedisAgentBus:
    """Redis-backed routing layer for cross-worker agent RPCs.

    Composed by :class:`AgentConnectionManager` alongside a
    :class:`LocalAgentTransport`. The bus does NOT touch Redis at
    construction time — :meth:`start` wires up the listener tasks
    and the heartbeat. This lets unit tests use the manager
    without bringing up a Redis instance, as long as those tests
    only exercise locally-owned agents.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        local: LocalAgentTransport,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        self.worker_id = worker_id
        self._local = local
        # Lazy redis: ``start()`` resolves it via ``get_redis()`` if
        # not injected, so unit tests using the local fast path do
        # not need a Redis client at all.
        self._redis: aioredis.Redis | None = redis_client
        self._command_task: asyncio.Task | None = None
        self._events_task: asyncio.Task | None = None
        self._stopping = False
        # Locally-owned agents whose registry TTL we must refresh.
        # Mirrors LocalAgentTransport._connections.keys() but the
        # bus tracks them explicitly so register / unregister are
        # cheap O(1) state transitions.
        self._owned_agents: set[str] = set()
        # In-flight dispatch tasks. We MUST hold strong references
        # because CPython's event loop only retains weak references
        # to tasks; a discarded ``asyncio.create_task`` result can
        # be garbage-collected mid-run on GIL pauses, causing the
        # remote dispatch to silently vanish. Each task removes
        # itself from this set via ``add_done_callback`` once it
        # finishes.
        self._dispatch_tasks: set[asyncio.Task] = set()

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Wire the bus to Redis and launch the background tasks.

        Idempotent: calling twice is a no-op. Resolves the redis
        client lazily via :func:`app.core.redis.get_redis` so the
        startup order in :func:`app.main.lifespan` does not matter
        as long as Redis is initialised before this method runs.
        """
        if self._command_task is not None:
            return
        if self._redis is None:
            from ..core.redis import get_redis
            self._redis = get_redis()
        self._stopping = False
        self._command_task = asyncio.create_task(
            self._command_listener(),
            name=f"agent-bus-command-{self.worker_id[:8]}",
        )
        self._events_task = asyncio.create_task(
            self._events_listener(),
            name=f"agent-bus-events-{self.worker_id[:8]}",
        )
        logger.info(
            "RedisAgentBus started (worker_id=%s)", self.worker_id,
        )

    async def stop(self) -> None:
        """Cancel listener tasks and best-effort drop our registry entries."""
        if self._command_task is None:
            return
        self._stopping = True
        # Wait for in-flight remote dispatches to publish their
        # responses before tearing down the listeners — otherwise a
        # peer worker that initiated send_request_remote will hit a
        # CommandTimeoutError instead of receiving the answer that
        # was a few microseconds away from being published.
        if self._dispatch_tasks:
            logger.info(
                "RedisAgentBus draining %d in-flight dispatch task(s)",
                len(self._dispatch_tasks),
            )
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
                pass  # Expected — we just cancelled it.
            except Exception:
                logger.exception(
                    "RedisAgentBus background task raised during stop",
                )
        self._command_task = None
        self._events_task = None
        # Drop the registry entries for agents this worker owned so
        # other workers see the takedown immediately instead of
        # waiting for the TTL.
        if self._redis is not None and self._owned_agents:
            for agent_id in list(self._owned_agents):
                with contextlib.suppress(Exception):
                    await _del_if_owner(
                        self._redis,
                        KEY_REGISTRY.format(agent_id=agent_id),
                        self.worker_id,
                    )
                    await self._redis.publish(
                        CHANNEL_EVENTS,
                        json.dumps({
                            "type": "disconnect",
                            "agent_id": agent_id,
                            "worker_id": self.worker_id,
                        }),
                    )
        logger.info(
            "RedisAgentBus stopped (worker_id=%s)", self.worker_id,
        )

    # ── Local register / unregister hooks ───────────────────────
    #
    # AgentConnectionManager.register / unregister call these AFTER
    # the LocalAgentTransport has updated its in-process state, so
    # by the time the bus publishes the connect / disconnect event
    # any other worker that observes the broadcast can immediately
    # check ``self._local.is_connected`` and trust the answer.

    async def on_local_register(self, agent_id: str) -> None:
        """Claim ownership of ``agent_id`` in Redis and broadcast a connect."""
        self._owned_agents.add(agent_id)
        if self._redis is None:
            return
        try:
            await self._redis.set(
                KEY_REGISTRY.format(agent_id=agent_id),
                self.worker_id,
                ex=REGISTRY_TTL_SECONDS,
            )
            await self._redis.publish(
                CHANNEL_EVENTS,
                json.dumps({
                    "type": "connect",
                    "agent_id": agent_id,
                    "worker_id": self.worker_id,
                }),
            )
        except Exception:
            logger.exception(
                "Failed to publish bus connect for agent %s", agent_id,
            )
            raise

    async def on_local_unregister(self, agent_id: str) -> None:
        """Release ownership of ``agent_id`` and broadcast a disconnect."""
        self._owned_agents.discard(agent_id)
        if self._redis is None:
            return
        try:
            await _del_if_owner(
                self._redis,
                KEY_REGISTRY.format(agent_id=agent_id),
                self.worker_id,
            )
            await self._redis.publish(
                CHANNEL_EVENTS,
                json.dumps({
                    "type": "disconnect",
                    "agent_id": agent_id,
                    "worker_id": self.worker_id,
                }),
            )
        except Exception:
            logger.exception(
                "Failed to publish bus disconnect for agent %s", agent_id,
            )
            raise

    # ── Owner discovery ─────────────────────────────────────────

    async def get_owner(self, agent_id: str) -> str | None:
        """Return the worker_id that owns ``agent_id``, or ``None``."""
        if self._redis is None:
            return None
        owner = await self._redis.get(KEY_REGISTRY.format(agent_id=agent_id))
        # ``decode_responses=True`` is set on the global client so
        # ``get`` returns str. We still guard against bytes for
        # tests that inject a custom client.
        if isinstance(owner, bytes):
            owner = owner.decode("utf-8")
        return owner

    async def is_remotely_connected(self, agent_id: str) -> bool:
        """Check whether *any* worker (local or remote) owns ``agent_id``."""
        if self._local.is_connected(agent_id):
            return True
        owner = await self.get_owner(agent_id)
        return owner is not None

    async def list_remote_agent_ids(self) -> list[str]:
        """Return all currently-registered agent ids across the cluster.

        Walks ``agent:registry:*`` via SCAN. The agent fleet is small
        (~tens of entries) so the scan is cheap and predictable.

        fakeredis has a documented bug where ``scan_iter`` after a
        ``WATCH``/``MULTI`` transaction returns ``None`` instead of
        a (cursor, items) tuple, which surfaces as a ``TypeError``
        in :func:`redis._parsers.helpers.parse_scan`. We catch ONLY
        that specific TypeError and fall back to ``KEYS`` so the
        unit tests pass without masking real Redis errors. Any
        other exception propagates per CLAUDE.md "no error hiding".
        """
        if self._redis is None:
            return self._local.get_connected_agent_ids()
        prefix = KEY_REGISTRY.format(agent_id="")
        ids: list[str] = []
        try:
            async for key in self._redis.scan_iter(match=f"{prefix}*"):
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                ids.append(key[len(prefix):])
        except TypeError:
            # fakeredis-only workaround. Real Redis never raises
            # TypeError from scan_iter, so this branch is dead in
            # production. We log so any future regression is
            # visible in operator logs.
            logger.warning(
                "scan_iter returned non-iterable; falling back to KEYS "
                "(fakeredis after WATCH/MULTI bug)",
            )
            keys = await self._redis.keys(f"{prefix}*") or []
            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                ids.append(key[len(prefix):])
        return ids

    # ── Outbound: send a request to a remote owner ──────────────

    async def send_request_remote(
        self,
        agent_id: str,
        msg_type: str,
        payload: dict,
        timeout: float = 60.0,
    ) -> dict:
        """Forward a request to whichever worker owns ``agent_id``.

        Caller is responsible for first checking that the agent is
        NOT locally owned — this method always goes through Redis.
        Raises :class:`AgentOfflineError` if the registry has no
        entry for the agent, :class:`CommandTimeoutError` if the
        owner does not publish a response in time, or the bus's
        translation of agent-side errors back to RuntimeError.
        """
        if self._redis is None:
            # The bus has not been started, so there is nobody else
            # who could possibly own the agent. Surface this as
            # offline rather than crash on a None client.
            raise AgentOfflineError(agent_id)

        owner = await self.get_owner(agent_id)
        if not owner:
            raise AgentOfflineError(agent_id)

        bus_request_id = uuid.uuid4().hex
        response_channel = CHANNEL_RESPONSE.format(request_id=bus_request_id)
        pubsub = self._redis.pubsub()
        # IMPORTANT: subscribe BEFORE rpush so the publish from the
        # owner cannot reach a non-existent subscriber. Redis pub/sub
        # has no buffering — see module docstring.
        await pubsub.subscribe(response_channel)
        try:
            envelope = {
                "request_id": bus_request_id,
                "agent_id": agent_id,
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
                    raise CommandTimeoutError(bus_request_id, timeout)
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
                # Translate the bus envelope's error_kind back into
                # the appropriate exception type so callers cannot
                # tell whether the request was served locally or
                # bounced through Redis.
                kind = response.get("error_kind")
                inner = response.get("payload") or {}
                if kind == "busy":
                    raise AgentBusyError(
                        agent_id,
                        per_agent=int(inner.get("per_agent", 0)),
                        global_pending=int(inner.get("global_pending", 0)),
                    )
                if kind == "offline":
                    raise AgentOfflineError(agent_id)
                if kind == "shutting_down":
                    raise AgentShuttingDownError()
                if kind == "timeout":
                    raise CommandTimeoutError(
                        bus_request_id, response.get("timeout", timeout),
                    )
                if kind == "runtime":
                    raise RuntimeError(inner.get("error", "unknown agent error"))
                if kind:
                    raise RuntimeError(
                        f"Bus returned unknown error_kind={kind!r}: {inner}"
                    )
                return inner
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(response_channel)
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    # ── Inbound: receive a remote request and dispatch locally ──

    async def _command_listener(self) -> None:
        """Background task: drain ``agent:cmd:{my_worker_id}`` and dispatch."""
        assert self._redis is not None
        key = KEY_CMD_QUEUE.format(worker_id=self.worker_id)
        # Best-effort cleanup of stale commands left over from a
        # crashed previous instance with the same worker_id (rare,
        # but possible if a deployment reuses worker ids). uuid4
        # should make this collision-free in practice.
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
                    "command listener BLPOP failed; backing off",
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
                    "Dropping malformed command envelope: %s", raw[:200],
                )
                continue
            # Dispatch as a separate task so the listener can
            # immediately go back to BLPOPping the next command.
            # The dispatch task self-handles its own exceptions
            # and publishes the response. **Anchor it in
            # ``self._dispatch_tasks`` so the GC cannot collect it
            # mid-run** — Python's event loop only holds weak
            # references to bare ``create_task`` results.
            task = asyncio.create_task(
                self._dispatch_remote_command(envelope),
                name=f"agent-bus-dispatch-{envelope.get('request_id', '?')[:8]}",
            )
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch_remote_command(self, envelope: dict[str, Any]) -> None:
        """Run a remote-originated command on our local transport."""
        assert self._redis is not None
        bus_request_id = envelope.get("request_id", "")
        agent_id = envelope.get("agent_id", "")
        msg_type = envelope.get("msg_type", "")
        payload = envelope.get("payload") or {}
        timeout = float(envelope.get("timeout", 60.0))
        response_channel = CHANNEL_RESPONSE.format(request_id=bus_request_id)

        if not bus_request_id:
            # Loud failure: an envelope without a request_id has no
            # response channel to publish on. Drop it after logging
            # so the operator can see the broken sender.
            logger.error(
                "Dropping bus envelope with no request_id "
                "(agent_id=%s, msg_type=%s)", agent_id, msg_type,
            )
            return

        response: dict[str, Any]
        try:
            if msg_type == "__send_raw__":
                # Cross-worker send_raw: caller wraps the payload as
                # ``{"payload": <real_payload>}`` so the bus envelope's
                # own ``payload`` slot is not shadowed. Unwrap and
                # forward to the local fire-and-forget path; the
                # response is an empty ack so the caller's
                # ``send_request_remote`` returns immediately.
                inner_payload = payload.get("payload") or {}
                await self._local.send_raw(agent_id, inner_payload)
                response = {"payload": {}}
            else:
                result = await self._local.send_request(
                    agent_id, msg_type, payload, timeout=timeout,
                )
                response = {"payload": result}
        except AgentBusyError as e:
            # Catch AgentBusyError BEFORE the RuntimeError handler
            # below — AgentBusyError subclasses RuntimeError but
            # carries enough information to be its own bucket.
            response = {
                "payload": {
                    "error": str(e),
                    "per_agent": e.per_agent,
                    "global_pending": e.global_pending,
                },
                "error_kind": "busy",
            }
        except AgentOfflineError:
            response = {
                "payload": {"error": f"Agent {agent_id} is offline"},
                "error_kind": "offline",
            }
        except AgentShuttingDownError:
            response = {
                "payload": {"error": "Worker is shutting down"},
                "error_kind": "shutting_down",
            }
        except CommandTimeoutError as e:
            response = {
                "payload": {"error": f"Timed out after {e.timeout}s"},
                "error_kind": "timeout",
                "timeout": e.timeout,
            }
        except RuntimeError as e:
            response = {
                "payload": {"error": str(e)},
                "error_kind": "runtime",
            }
        except Exception as e:
            logger.exception(
                "Unhandled error dispatching remote command "
                "(request_id=%s, agent_id=%s, msg_type=%s)",
                bus_request_id, agent_id, msg_type,
            )
            response = {
                "payload": {"error": f"{type(e).__name__}: {e}"},
                "error_kind": "runtime",
            }

        try:
            await self._redis.publish(response_channel, json.dumps(response))
        except Exception:
            logger.exception(
                "Failed to publish bus response on %s", response_channel,
            )

    # ── Events: cross-worker connect/disconnect broadcasts ──────

    async def _events_listener(self) -> None:
        """Background task: forward ``agent:events`` to local waiters."""
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
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    logger.exception(
                        "Dropping malformed agent event: %s", data[:200],
                    )
                    continue
                event_type = event.get("type")
                agent_id = event.get("agent_id")
                if not agent_id:
                    continue
                if event_type == "connect":
                    # Skip self-broadcasts: register() already woke
                    # local waiters synchronously, and re-firing them
                    # via the bus loop is harmless but noisy.
                    if event.get("worker_id") == self.worker_id:
                        continue
                    # Wake local connect waiters so AgentConnectionManager
                    # .wait_for_connection (which uses LocalAgentTransport's
                    # waiter set) sees the cross-worker register too.
                    self._wake_local_connect_waiters(agent_id)
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(CHANNEL_EVENTS)
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    def _wake_local_connect_waiters(self, agent_id: str) -> None:
        """Wake the LocalAgentTransport's connect waiters for ``agent_id``.

        Used by the events listener to forward cross-worker connect
        broadcasts into the same per-agent waiter mechanism that the
        local register() path uses, so a single
        :meth:`AgentConnectionManager.wait_for_connection` call covers
        both local and remote registers.
        """
        self._local.wake_connect_waiters(agent_id)

    # ── Ping-driven TTL refresh ──────────────────────────────────

    async def refresh_agent_registration(self, agent_id: str) -> None:
        """Reset the registry TTL for ``agent_id`` on receiving a ping.

        Called by the WebSocket handler each time the agent sends a
        ``{"type": "ping"}`` frame. This keeps the registry key alive
        without a background heartbeat loop: TTL = 90 s (3× the 30 s
        agent ping interval) so one missed ping does not immediately
        flip the agent offline.

        Only refreshes if this worker is the current owner (the key
        value must match our worker_id). If another worker won a race
        reconnect, we silently skip — the new owner will refresh on
        its own pings.
        """
        if self._redis is None or agent_id not in self._owned_agents:
            return
        key = KEY_REGISTRY.format(agent_id=agent_id)
        try:
            # SET NX would race; use a plain EXPIRE guarded by a GET
            # comparison. If the key was taken over by another worker
            # between the GET and EXPIRE, the worst outcome is that we
            # slightly extend the TTL on the new owner's entry — a
            # harmless false-positive. The old WATCH/MULTI approach is
            # unnecessary here because a false-positive EXPIRE never
            # causes split-brain (only _del_if_owner needs the atomic
            # delete guarantee).
            owner = await self._redis.get(key)
            if isinstance(owner, bytes):
                owner = owner.decode("utf-8")
            if owner == self.worker_id:
                await self._redis.expire(key, REGISTRY_TTL_SECONDS)
        except Exception:
            logger.exception(
                "refresh_agent_registration failed for agent %s", agent_id,
            )
