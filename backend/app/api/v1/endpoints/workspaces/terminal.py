"""Web Terminal browser WebSocket endpoint.

Issues short-lived single-use tickets to authenticated browsers and
relays PTY I/O between the browser and the agent via session_id-keyed
routing in :mod:`services.terminal_router`.

Auth model:
- ``POST /workspaces/terminal/ticket`` — JWT (cookie/Bearer) required,
  admin-only, agent ownership verified. Returns a 30s ticket.
- ``WS /workspaces/terminal/ws?ticket=...`` — ticket consumed on accept.
  Origin allowlist checked BEFORE accept (CSWSH defense).
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel

from .....core.config import settings
from .....core.deps import get_current_user
from .....core.redis import get_redis
from .....models import User
from .....models.remote import RemoteAgent
from .....services.agent_manager import (
    AgentOfflineError,
    agent_manager,
)
from .....services.terminal_router import terminal_router

logger = logging.getLogger(__name__)

router = APIRouter()


# Redis-backed ticket store. The previous in-memory dict broke under
# ``WEB_CONCURRENCY>1`` because the worker that issued the ticket is
# rarely the worker that handles the follow-up WebSocket — the lookup
# returned ``None`` and the WS was rejected with 4008 (which Starlette
# surfaces as HTTP 403). Redis is already a hard dependency, so the
# extra hop is free.
TICKET_TTL_SECONDS = 30
_TICKET_KEY_PREFIX = "terminal:ticket:"


async def _store_ticket(ticket: str, user_id: str, agent_id: str) -> None:
    redis = get_redis()
    await redis.setex(
        f"{_TICKET_KEY_PREFIX}{ticket}",
        TICKET_TTL_SECONDS,
        json.dumps({"user_id": user_id, "agent_id": agent_id}),
    )


async def _consume_ticket(ticket: str) -> tuple[str, str] | None:
    redis = get_redis()
    # GETDEL is atomic on Redis 6.2+ and matches the single-use semantics
    # we want — the ticket is invalidated even if two browser tabs race.
    raw = await redis.execute_command("GETDEL", f"{_TICKET_KEY_PREFIX}{ticket}")
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    user_id = data.get("user_id")
    agent_id = data.get("agent_id")
    if not user_id or not agent_id:
        return None
    return user_id, agent_id


class TicketRequest(BaseModel):
    agent_id: str


class TicketResponse(BaseModel):
    ticket: str
    expires_in: int


@router.post("/terminal/ticket", response_model=TicketResponse)
async def issue_terminal_ticket(
    body: TicketRequest,
    user: User = Depends(get_current_user),
) -> TicketResponse:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin required",
        )
    agent = await RemoteAgent.get(body.agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    ticket = secrets.token_urlsafe(32)
    await _store_ticket(ticket, str(user.id), body.agent_id)
    return TicketResponse(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


# ── Phase A: session list + kill REST endpoints ────────────────


async def _check_agent_owned(agent_id: str, user: User) -> RemoteAgent:
    """Verify the user owns the agent; same shape as the ticket
    endpoint's agent lookup so callers see consistent 403 / 404
    semantics."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin required",
        )
    agent = await RemoteAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return agent


@router.get("/terminal/{agent_id}/sessions")
async def list_terminal_sessions(
    agent_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """List every PTY session currently held by the agent.

    Each entry includes ``session_id``, ``started_at``,
    ``last_activity``, ``cmdline`` (the shell), and ``alive``.
    """
    await _check_agent_owned(agent_id, user)
    try:
        result = await agent_manager.send_request(
            agent_id, "terminal_list", {}, timeout=10.0,
        )
    except AgentOfflineError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent offline",
        ) from exc
    if not isinstance(result, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent returned an unexpected response",
        )
    return {"sessions": result.get("sessions") or []}


@router.delete(
    "/terminal/{agent_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def kill_terminal_session(
    agent_id: str,
    session_id: str,
    user: User = Depends(get_current_user),
) -> None:
    """Kill a live PTY session on the agent.

    Returns 204 on success. 404 if the agent doesn't have the
    session (already exited or never existed).
    """
    await _check_agent_owned(agent_id, user)
    try:
        result = await agent_manager.send_request(
            agent_id, "terminal_kill",
            {"session_id": session_id}, timeout=10.0,
        )
    except AgentOfflineError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent offline",
        ) from exc
    if not isinstance(result, dict) or not result.get("success"):
        err = (result or {}).get("error") or "session not found"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=err,
        )


def _allowed_origins() -> set[str]:
    return settings.ws_allowed_origins


@router.websocket("/terminal/ws")
async def terminal_websocket(ws: WebSocket):
    """Browser WebSocket for the Web Terminal.

    The ticket is consumed (one-shot) on accept. After that the session
    is identified by a server-generated ``session_id`` that the agent
    echoes back on every PTY frame.
    """
    origin = ws.headers.get("origin")
    if origin is not None:
        if origin not in _allowed_origins():
            logger.warning(
                "terminal_websocket: rejecting Origin=%r", origin,
            )
            await ws.close(code=4403, reason="Origin not allowed")
            return

    ticket = ws.query_params.get("ticket")
    shell = ws.query_params.get("shell", "")
    if not ticket:
        await ws.close(code=4008, reason="ticket required")
        return

    consumed = await _consume_ticket(ticket)
    if consumed is None:
        await ws.close(code=4008, reason="ticket invalid or expired")
        return
    _user_id, agent_id = consumed

    await ws.accept()

    # Phase A: an existing session_id in the query string means
    # ``attach`` (replay scrollback + share live output); no
    # session_id means ``create`` (spawn a fresh PTY).
    requested_session_id = ws.query_params.get("session_id")
    is_attach = bool(requested_session_id)
    session_id = requested_session_id or uuid.uuid4().hex
    terminal_router.register(session_id, ws)

    try:
        cols = int(ws.query_params.get("cols", 120))
        rows = int(ws.query_params.get("rows", 40))
    except ValueError:
        cols, rows = 120, 40

    rpc_type, rpc_payload = (
        ("terminal_attach", {"session_id": session_id})
        if is_attach
        else (
            "terminal_create",
            {
                "session_id": session_id,
                "shell": shell,
                "cols": cols,
                "rows": rows,
            },
        )
    )

    try:
        result = await agent_manager.send_request(
            agent_id, rpc_type, rpc_payload, timeout=10.0,
        )
    except AgentOfflineError:
        terminal_router.unregister(session_id, ws)
        try:
            await ws.send_text(json.dumps({
                "type": "error", "message": "Agent offline",
            }))
        except Exception:
            logger.info("terminal_websocket: could not deliver offline notice", exc_info=True)
        await ws.close(code=4500, reason="Agent offline")
        return
    except Exception as e:
        terminal_router.unregister(session_id, ws)
        logger.exception(
            "terminal_websocket: send_request failed agent=%s session=%s rpc=%s",
            agent_id, session_id, rpc_type,
        )
        try:
            await ws.send_text(json.dumps({
                "type": "error", "message": f"PTY open failed: {e}",
            }))
        except Exception:
            logger.info("terminal_websocket: could not deliver error notice", exc_info=True)
        await ws.close(code=4500, reason="PTY open failed")
        return

    if not isinstance(result, dict) or not result.get("success"):
        terminal_router.unregister(session_id, ws)
        msg = (result or {}).get(
            "error",
            "session not found" if is_attach else "PTY open failed",
        )
        try:
            await ws.send_text(json.dumps({
                "type": "error", "message": msg,
            }))
        except Exception:
            logger.info("terminal_websocket: could not deliver agent-error", exc_info=True)
        await ws.close(
            code=4404 if is_attach else 4500,
            reason=msg[:100],
        )
        return

    started_msg: dict = {
        "type": "session_started",
        "session_id": session_id,
        "attached": is_attach,
    }
    if is_attach:
        # Forward the agent's scrollback so the frontend can restore
        # the screen before any new live output arrives.
        started_msg["scrollback"] = result.get("scrollback") or []
        started_msg["cmdline"] = result.get("cmdline")
        started_msg["started_at"] = result.get("started_at")
        started_msg["exited"] = result.get("exited", False)
    else:
        started_msg["shell"] = result.get("shell")
    try:
        await ws.send_text(json.dumps(started_msg))
    except Exception:
        logger.exception(
            "terminal_websocket: failed to send session_started session=%s",
            session_id,
        )
        terminal_router.unregister(session_id, ws)
        return

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "terminal_websocket: dropped non-JSON frame session=%s",
                    session_id,
                )
                continue

            msg_type = msg.get("type")

            if msg_type == "input":
                data = msg.get("data", "")
                if isinstance(data, str):
                    await agent_manager.send_raw(agent_id, {
                        "type": "terminal_input",
                        "payload": {"session_id": session_id, "data": data},
                    })
            elif msg_type == "resize":
                try:
                    new_cols = int(msg.get("cols", 120))
                    new_rows = int(msg.get("rows", 40))
                except (TypeError, ValueError):
                    continue
                await agent_manager.send_raw(agent_id, {
                    "type": "terminal_resize",
                    "payload": {
                        "session_id": session_id,
                        "cols": new_cols,
                        "rows": new_rows,
                    },
                })
            elif msg_type == "ping":
                try:
                    await ws.send_text(json.dumps({"type": "pong"}))
                except Exception:
                    logger.info(
                        "terminal_websocket: pong send failed session=%s",
                        session_id, exc_info=True,
                    )
                    break
            else:
                logger.warning(
                    "terminal_websocket: unknown msg_type=%r session=%s",
                    msg_type, session_id,
                )

    except WebSocketDisconnect:
        logger.info(
            "terminal_websocket: browser disconnected session=%s", session_id,
        )
    except Exception:
        logger.exception(
            "terminal_websocket: unexpected error session=%s", session_id,
        )
    finally:
        terminal_router.unregister(session_id, ws)
        # Phase A: browser disconnect = detach (session keeps running
        # so the operator can reattach later). Explicit termination
        # uses the new ``DELETE`` REST endpoint or
        # ``terminal_kill`` over a different code path.
        try:
            await agent_manager.send_raw(agent_id, {
                "type": "terminal_detach",
                "payload": {"session_id": session_id},
            })
        except Exception:
            logger.info(
                "terminal_websocket: terminal_detach send failed agent=%s session=%s",
                agent_id, session_id, exc_info=True,
            )
