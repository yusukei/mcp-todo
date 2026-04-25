"""Agent WebSocket loop.

The WebSocket endpoint authenticates with a first-message ``auth``
frame and then multiplexes:
- request/response for remote execution (routed by ``request_id`` via
  ``agent_manager.resolve_request``)
- agent_info updates and update_available push
- terminal_output / terminal_exit forwarding to
  ``services.terminal_router`` (Web Terminal session push)

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

from .....core.config import settings
from .....core.security import hash_api_key
from .....models.remote import RemoteAgent
from .....services.agent_manager import agent_manager
from ._releases_util import maybe_push_update

logger = logging.getLogger(__name__)

router = APIRouter()

async def _safe_close(ws: WebSocket, *, code: int, reason: str) -> None:
    """Best-effort ``ws.close`` that logs — never silently swallows — failures.

    Close is called from error paths where the connection may already
    be half-dead, so a close failure is usually benign (the socket is
    going away anyway). We still emit a debug-level log with
    ``exc_info`` so ws lifecycle anomalies are visible to operators
    instead of vanishing into an ``except: pass``.
    """
    try:
        await ws.close(code=code, reason=reason)
    except (RuntimeError, OSError, WebSocketDisconnect) as e:
        logger.info(
            "agent_websocket: ws.close(code=%s) failed (already closed?): %s",
            code, e, exc_info=e,
        )


def _allowed_origins() -> set[str]:
    """Return the allowlist from settings.

    Derived from ``FRONTEND_URL`` via ``settings.ws_allowed_origins``.
    Re-evaluated on each request so test fixtures can patch settings
    without re-importing the module.
    """
    return settings.ws_allowed_origins


@router.websocket("/agent/ws")
async def agent_websocket(ws: WebSocket):
    """Agent WebSocket with first-message authentication.

    The Origin header is validated **before** ``ws.accept()`` to defend
    against browser-mediated CSWSH attacks. Server-to-server agent
    clients (which do not send an Origin header) are still permitted —
    CSWSH is exclusively a browser-mediated attack vector and the agent
    auth token is the security boundary for non-browser callers.
    """
    origin = ws.headers.get("origin")
    if origin is not None:
        # Browser-originated request: must match the configured allowlist.
        allowed = _allowed_origins()
        if origin not in allowed:
            logger.warning(
                "agent_websocket: rejecting connection with disallowed Origin=%r", origin
            )
            await ws.close(code=4403, reason="Origin not allowed")
            return

    await ws.accept()

    # ── Auth via first message ──
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect) as e:
        logger.info("agent_websocket: auth handshake failed: %s", e, exc_info=e)
        await _safe_close(ws, code=4008, reason="Auth timeout")
        return

    if msg.get("type") != "auth" or not msg.get("token"):
        logger.warning(
            "agent_websocket: first message was not an auth frame (type=%r)",
            msg.get("type"),
        )
        await _safe_close(ws, code=4008, reason="Expected auth message")
        return

    key_hash = hash_api_key(msg["token"])
    agent = await RemoteAgent.find_one({"key_hash": key_hash})
    if not agent:
        logger.warning("agent_websocket: rejected connection with invalid token")
        try:
            await ws.send_text(json.dumps({"type": "auth_error", "message": "Invalid token"}))
        except (RuntimeError, OSError, WebSocketDisconnect) as e:
            logger.info(
                "agent_websocket: could not deliver auth_error frame: %s",
                e, exc_info=e,
            )
        await _safe_close(ws, code=4008, reason="Invalid agent token")
        return

    agent_id = str(agent.id)
    await ws.send_text(json.dumps({"type": "auth_ok", "agent_id": agent_id}))

    await agent_manager.register(agent_id, ws)

    agent.last_seen_at = datetime.now(UTC)
    await agent.save()
    logger.info("Agent connected: %s (%s)", agent.name, agent_id)

    # ── Message loop ──
    try:
        while True:
            raw = await ws.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                # Loudly surface protocol drift instead of silently
                # dropping the frame. Per CLAUDE.md "No error hiding":
                # malformed input is information the operator needs,
                # not something to throw away on the floor.
                logger.warning(
                    "Agent %s: dropped non-JSON frame: %r",
                    agent_id, raw[:200],
                )
                continue

            msg_type = msg.get("type")
            request_id = msg.get("request_id")

            # If this message correlates to a pending RPC request, resolve
            # the Future and stop. Server-pushed messages (terminal_output
            # / terminal_exit) may also carry a request_id but have no
            # pending Future, so ``resolve_request`` returns False and we
            # fall through to the type-based dispatch below.
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
                # ``host_id`` joins the agent record with the supervisor
                # record running on the same physical host. Optional —
                # legacy agents without the field keep ``host_id=""``.
                reported_host_id = msg.get("host_id")
                if reported_host_id:
                    agent.host_id = reported_host_id
                agent.last_seen_at = datetime.now(UTC)
                await agent.save()
                # Check for available updates *after* persisting the
                # reported version so the comparison uses fresh data.
                await maybe_push_update(ws, agent)

            elif msg_type == "ping":
                # Agent-initiated heartbeat. Respond immediately so the
                # agent's send confirms the connection is alive, then
                # refresh the Redis TTL and update last_seen_at.
                await ws.send_text(json.dumps({"type": "pong"}))
                await agent_manager.refresh_agent_registration(agent_id)
                agent.last_seen_at = datetime.now(UTC)
                await agent.save()

            elif msg_type in ("terminal_output", "terminal_exit"):
                from .....services.terminal_router import terminal_router
                await terminal_router.dispatch(msg)

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
    except Exception:
        # Agent WebSocket dispatcher is the protocol boundary for the
        # inner message loop; we must log the full traceback before
        # letting ``finally`` tear the connection down. CLAUDE.md
        # forbids ``logger.error(..., e)`` without ``exc_info``.
        logger.exception("Agent WebSocket error (%s)", agent_id)
    finally:
        await agent_manager.unregister(agent_id, ws)  # Only remove if this is still the current connection
        agent.last_seen_at = datetime.now(UTC)
        try:
            await agent.save()
        except Exception:
            # Cleanup path: the WS is already tearing down. A failure
            # to persist last_seen_at is a monitoring concern, not a
            # recoverable one — log the full traceback so it is
            # visible, but do not re-raise (would mask the triggering
            # disconnect in the task's parent scope).
            logger.exception(
                "Failed to persist agent last_seen_at on disconnect (%s)",
                agent_id,
            )
