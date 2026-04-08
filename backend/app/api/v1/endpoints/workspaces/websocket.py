"""Agent WebSocket loop.

The WebSocket endpoint authenticates with a first-message ``auth``
frame and then multiplexes:
- request/response for remote execution (routed by ``request_id`` via
  ``agent_manager.resolve_request``)
- agent_info updates and update_available push
- chat event forwarding to ``services.chat_events``

Routing by ``request_id`` is the single source of truth for "is this an
RPC response". With the envelope redesign (2026-04-08), inner result
data is nested under a ``payload`` key so envelope fields (``type``,
``request_id``) cannot be shadowed by handler data — but we still route
by ``request_id`` because it is the actual correlation key, not the
``type`` field.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .....core.security import hash_api_key
from .....models.remote import RemoteAgent
from .....services.agent_manager import agent_manager
from ._releases_util import maybe_push_update
from ._shared import PING_INTERVAL, PING_TIMEOUT

logger = logging.getLogger(__name__)

router = APIRouter()


async def _server_ping_loop(ws: WebSocket, agent_id: str) -> None:
    """Send periodic pings to detect dead connections from the server side."""
    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            await asyncio.wait_for(
                ws.send_text(json.dumps({"type": "ping"})),
                timeout=PING_TIMEOUT,
            )
        except Exception:
            logger.info("Agent %s: ping failed, connection appears dead", agent_id)
            break


@router.websocket("/agent/ws")
async def agent_websocket(ws: WebSocket):
    """Agent WebSocket with first-message authentication."""
    await ws.accept()

    # ── Auth via first message ──
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        try:
            await ws.close(code=4008, reason="Auth timeout")
        except Exception:
            pass
        return

    if msg.get("type") != "auth" or not msg.get("token"):
        try:
            await ws.close(code=4008, reason="Expected auth message")
        except Exception:
            pass
        return

    key_hash = hash_api_key(msg["token"])
    agent = await RemoteAgent.find_one({"key_hash": key_hash})
    if not agent:
        try:
            await ws.send_text(json.dumps({"type": "auth_error", "message": "Invalid token"}))
            await ws.close(code=4008, reason="Invalid agent token")
        except Exception:
            pass
        return

    agent_id = str(agent.id)
    await ws.send_text(json.dumps({"type": "auth_ok", "agent_id": agent_id}))

    # Register and close old connection if replaced
    old_ws = agent_manager.register(agent_id, ws)
    if old_ws is not None:
        try:
            await old_ws.close(code=1012, reason="Replaced by new connection")
        except Exception:
            pass

    agent.is_online = True
    agent.last_seen_at = datetime.now(UTC)
    await agent.save()
    logger.info("Agent connected: %s (%s)", agent.name, agent_id)

    # ── Server-side ping task for dead connection detection ──
    ping_task = asyncio.create_task(_server_ping_loop(ws, agent_id))

    # ── Message loop ──
    try:
        while True:
            raw = await ws.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            request_id = msg.get("request_id")

            # If this message correlates to a pending RPC request, resolve
            # the Future and stop. Server-pushed messages (chat_event /
            # chat_complete / chat_error) may also carry a request_id but
            # have no pending Future, so ``resolve_request`` returns False
            # and we fall through to the type-based dispatch below.
            if request_id is not None and agent_manager.resolve_request(msg):
                continue

            if msg_type == "agent_info":
                agent.hostname = msg.get("hostname", agent.hostname)
                agent.os_type = msg.get("os", agent.os_type)
                agent.available_shells = msg.get("shells", agent.available_shells)
                # New: agent reports its version on every (re)connection.
                reported_version = msg.get("agent_version")
                if reported_version:
                    agent.agent_version = reported_version
                agent.last_seen_at = datetime.now(UTC)
                await agent.save()
                # Check for available updates *after* persisting the
                # reported version so the comparison uses fresh data.
                await maybe_push_update(ws, agent)

            elif msg_type == "pong":
                pass

            elif msg_type in ("chat_event", "chat_complete", "chat_error"):
                from .....services.chat_events import handle_chat_event
                asyncio.ensure_future(handle_chat_event(msg))

            else:
                # Loudly surface protocol drift instead of silently
                # dropping. This is the trap that the request_id-based
                # dispatch above is designed to avoid for RPC responses;
                # for genuinely unknown server-push types we still want
                # the operator to see them.
                logger.warning(
                    "Agent %s: unknown message type=%r request_id=%s (dropped)",
                    agent_id, msg_type, request_id,
                )

    except WebSocketDisconnect:
        logger.info("Agent disconnected: %s (%s)", agent.name, agent_id)
    except Exception as e:
        logger.error("Agent WebSocket error: %s", e)
    finally:
        ping_task.cancel()
        agent_manager.unregister(agent_id, ws)  # Only remove if this is still the current connection
        agent.is_online = False
        agent.last_seen_at = datetime.now(UTC)
        try:
            await agent.save()
        except Exception:
            pass
