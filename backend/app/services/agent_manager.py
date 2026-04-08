"""Agent connection manager — process-wide singleton for remote agent WebSockets.

Extracted from `api/v1/endpoints/workspaces/` so that:
- `mcp/tools/remote.py` can import it without lazy imports / circular deps
- `api/v1/endpoints/chat.py` can dispatch agent payloads via a public method
  instead of poking at the private `_connections` dict.

The class is intentionally framework-agnostic regarding HTTP routing — only the
WebSocket protocol surface is referenced (send_text), so it can be unit-tested
with a fake WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class AgentOfflineError(Exception):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        super().__init__(f"Agent {agent_id} is offline")


class CommandTimeoutError(Exception):
    def __init__(self, request_id: str, timeout: float):
        self.request_id = request_id
        self.timeout = timeout
        super().__init__(f"Request {request_id} timed out after {timeout}s")


class AgentConnectionManager:
    """Manages agent WebSocket connections and request/response exchanges.

    Single instance per process. All methods run on the same event loop.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        # request_id → (agent_id, Future)
        self._pending: dict[str, tuple[str, asyncio.Future]] = {}
        # agent_id → set[Event] — woken when the agent connects/reconnects
        self._connect_waiters: dict[str, set[asyncio.Event]] = {}

    def register(self, agent_id: str, ws: WebSocket) -> WebSocket | None:
        """Register a new connection, returning the old one if replaced."""
        old_ws = self._connections.get(agent_id)
        self._connections[agent_id] = ws
        if old_ws is not None and old_ws is not ws:
            logger.warning("Agent %s: replacing existing connection (reconnect)", agent_id)
        # Wake up anybody waiting on `wait_for_connection(agent_id)`
        waiters = self._connect_waiters.pop(agent_id, None)
        if waiters:
            for event in waiters:
                event.set()
        if old_ws is not None and old_ws is not ws:
            return old_ws
        return None

    def unregister(self, agent_id: str, ws: WebSocket | None = None) -> None:
        """Unregister a connection.

        If ws is provided, only removes if it matches the current connection
        (prevents a stale handler from removing a newer reconnection).
        If ws is None, unconditionally removes (used by delete_agent).
        """
        if ws is not None:
            current = self._connections.get(agent_id)
            if current is not ws:
                # This is a stale handler — the agent already reconnected
                logger.debug("Agent %s: skipping stale unregister", agent_id)
                return
        self._connections.pop(agent_id, None)
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
        """
        if timeout <= 0:
            return self.is_connected(agent_id)
        if self.is_connected(agent_id):
            return True
        event = asyncio.Event()
        self._connect_waiters.setdefault(agent_id, set()).add(event)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self.is_connected(agent_id)
        except asyncio.TimeoutError:
            return False
        finally:
            waiters = self._connect_waiters.get(agent_id)
            if waiters:
                waiters.discard(event)
                if not waiters:
                    self._connect_waiters.pop(agent_id, None)

    async def send_raw(self, agent_id: str, payload: dict[str, Any]) -> None:
        """Send a fire-and-forget JSON payload to an agent (no response awaited).

        Use this for one-way messages like chat dispatch / cancel where the
        response arrives asynchronously through other channels (e.g. chat events).
        Raises AgentOfflineError if the agent is not connected.
        """
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
        """
        ws = self._connections.get(agent_id)
        if not ws and wait_for_agent > 0:
            if await self.wait_for_connection(agent_id, wait_for_agent):
                ws = self._connections.get(agent_id)
        if not ws:
            raise AgentOfflineError(agent_id)

        # Full 128-bit UUID. The WebSocket dispatcher routes responses
        # purely by request_id, so a predictable or collision-prone id
        # would let one in-flight request's Future be resolved by
        # another's response. Do not shorten this.
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = (agent_id, future)

        try:
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
