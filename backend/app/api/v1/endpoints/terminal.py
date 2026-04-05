"""Terminal remote access — REST endpoints + WebSocket relay."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ....core.deps import get_admin_user
from ....core.redis import get_redis
from ....core.security import hash_api_key
from ....models import User
from ....models.terminal import TerminalAgent, TerminalSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/terminal", tags=["terminal"])

# ── Ticket management (reuse SSE pattern) ────────────────────

_TICKET_TTL = 30  # seconds
_TICKET_PREFIX = "terminal_ticket:"

# ── In-memory relay state ────────────────────────────────────

# agent_id (str) → WebSocket
_agent_connections: dict[str, WebSocket] = {}

# agent_id (str) → browser WebSocket (MVP: 1 agent = 1 session)
_browser_sessions: dict[str, WebSocket] = {}

# agent_id (str) → TerminalSession id
_active_sessions: dict[str, str] = {}


# ── Request / Response schemas ───────────────────────────────

class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class AgentResponse(BaseModel):
    id: str
    name: str
    hostname: str
    os_type: str
    available_shells: list[str]
    is_online: bool
    last_seen_at: str | None
    created_at: str


class AgentCreatedResponse(AgentResponse):
    token: str  # only returned once at creation


class TicketResponse(BaseModel):
    ticket: str


# ── Helper ───────────────────────────────────────────────────

def _agent_dict(a: TerminalAgent) -> dict:
    return {
        "id": str(a.id),
        "name": a.name,
        "hostname": a.hostname,
        "os_type": a.os_type,
        "available_shells": a.available_shells,
        "is_online": str(a.id) in _agent_connections,
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        "created_at": a.created_at.isoformat(),
    }


# ── Health check (no auth, for deployment verification) ──────

@router.get("/health")
async def terminal_health() -> dict:
    return {"status": "ok", "websocket_endpoints": ["/agent/ws", "/session/ws"]}


# ── REST endpoints (admin only) ──────────────────────────────

@router.get("/agents")
async def list_agents(user: User = Depends(get_admin_user)) -> list[dict]:
    agents = await TerminalAgent.find(
        {"owner_id": str(user.id)}
    ).sort("-created_at").to_list()
    return [_agent_dict(a) for a in agents]


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(body: CreateAgentRequest, user: User = Depends(get_admin_user)) -> dict:
    raw_token = f"ta_{secrets.token_hex(32)}"
    agent = TerminalAgent(
        name=body.name,
        key_hash=hash_api_key(raw_token),
        owner_id=str(user.id),
    )
    await agent.insert()
    return {
        **_agent_dict(agent),
        "token": raw_token,
    }


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: User = Depends(get_admin_user)) -> None:
    agent = await TerminalAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    # Close active WebSocket if connected
    ws = _agent_connections.pop(str(agent.id), None)
    if ws:
        try:
            await ws.close(code=1000, reason="Agent deleted")
        except Exception:
            pass
    await agent.delete()


@router.post("/ticket", response_model=TicketResponse)
async def create_terminal_ticket(user: User = Depends(get_admin_user)) -> TicketResponse:
    """Issue a short-lived ticket for WebSocket connection."""
    ticket = uuid.uuid4().hex
    redis = get_redis()
    await redis.set(f"{_TICKET_PREFIX}{ticket}", str(user.id), ex=_TICKET_TTL)
    return TicketResponse(ticket=ticket)


# ── WebSocket: Agent connection ──────────────────────────────

@router.websocket("/agent/ws")
async def agent_websocket(ws: WebSocket, token: str = Query(...)):
    """WebSocket endpoint for remote agents."""
    # Authenticate agent by token
    key_hash = hash_api_key(token)
    agent = await TerminalAgent.find_one({"key_hash": key_hash})
    if not agent:
        await ws.close(code=4008, reason="Invalid agent token")
        return

    await ws.accept()
    agent_id = str(agent.id)
    _agent_connections[agent_id] = ws

    # Update online status
    agent.is_online = True
    agent.last_seen_at = datetime.now(UTC)
    await agent.save()
    logger.info("Agent connected: %s (%s)", agent.name, agent_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "agent_info":
                # Update agent metadata
                agent.hostname = msg.get("hostname", agent.hostname)
                agent.os_type = msg.get("os", agent.os_type)
                agent.available_shells = msg.get("shells", agent.available_shells)
                agent.last_seen_at = datetime.now(UTC)
                await agent.save()

            elif msg_type == "output":
                # Forward PTY output to browser
                browser_ws = _browser_sessions.get(agent_id)
                if browser_ws:
                    try:
                        await browser_ws.send_text(json.dumps({
                            "type": "output",
                            "data": msg.get("data", ""),
                        }))
                    except Exception:
                        _browser_sessions.pop(agent_id, None)

            elif msg_type == "exited":
                # PTY process exited
                browser_ws = _browser_sessions.pop(agent_id, None)
                session_id = _active_sessions.pop(agent_id, None)
                if browser_ws:
                    try:
                        await browser_ws.send_text(json.dumps({
                            "type": "session_ended",
                            "reason": "process_exited",
                            "exit_code": msg.get("exit_code", -1),
                        }))
                    except Exception:
                        pass
                if session_id:
                    session = await TerminalSession.get(session_id)
                    if session:
                        session.ended_at = datetime.now(UTC)
                        await session.save()

            elif msg_type == "pong":
                agent.last_seen_at = datetime.now(UTC)
                # Throttle DB writes — only update every 60s
                pass

    except WebSocketDisconnect:
        logger.info("Agent disconnected: %s (%s)", agent.name, agent_id)
    except Exception as e:
        logger.error("Agent WebSocket error: %s", e)
    finally:
        _agent_connections.pop(agent_id, None)
        agent.is_online = False
        agent.last_seen_at = datetime.now(UTC)
        try:
            await agent.save()
        except Exception:
            pass

        # Notify any connected browser
        browser_ws = _browser_sessions.pop(agent_id, None)
        session_id = _active_sessions.pop(agent_id, None)
        if browser_ws:
            try:
                await browser_ws.send_text(json.dumps({
                    "type": "session_ended",
                    "reason": "agent_disconnect",
                }))
            except Exception:
                pass
        if session_id:
            try:
                session = await TerminalSession.get(session_id)
                if session:
                    session.ended_at = datetime.now(UTC)
                    await session.save()
            except Exception:
                pass


# ── WebSocket: Browser session ───────────────────────────────

@router.websocket("/session/ws")
async def browser_websocket(
    ws: WebSocket,
    ticket: str = Query(...),
    agent_id: str = Query(...),
    shell: str = Query(""),
):
    """WebSocket endpoint for browser terminal sessions."""
    # Validate ticket
    redis = get_redis()
    ticket_key = f"{_TICKET_PREFIX}{ticket}"
    user_id = await redis.get(ticket_key)
    if not user_id:
        await ws.close(code=4001, reason="Invalid or expired ticket")
        return
    await redis.delete(ticket_key)

    # Verify user
    user = await User.get(user_id)
    if not user or not user.is_active or not user.is_admin:
        await ws.close(code=4003, reason="Unauthorized")
        return

    # Verify agent exists and is online
    agent = await TerminalAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        await ws.close(code=4004, reason="Agent not found")
        return

    agent_ws = _agent_connections.get(agent_id)
    if not agent_ws:
        await ws.close(code=4005, reason="Agent is offline")
        return

    # Check if agent already has an active session
    if agent_id in _browser_sessions:
        await ws.close(code=4006, reason="Agent already has an active session")
        return

    await ws.accept()
    _browser_sessions[agent_id] = ws

    # Create audit session
    session = TerminalSession(
        agent_id=agent_id,
        user_id=str(user.id),
        shell=shell or "",
    )
    await session.insert()
    _active_sessions[agent_id] = str(session.id)

    # Tell agent to start PTY
    try:
        await agent_ws.send_text(json.dumps({
            "type": "session_start",
            "shell": shell or "",
            "cols": 120,
            "rows": 40,
        }))
        await ws.send_text(json.dumps({
            "type": "session_started",
            "shell": shell or "",
        }))
    except Exception as e:
        logger.error("Failed to start session: %s", e)
        _browser_sessions.pop(agent_id, None)
        _active_sessions.pop(agent_id, None)
        await ws.close(code=1011, reason="Failed to start session")
        return

    logger.info("Terminal session started: user=%s agent=%s", user.name, agent.name)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "input":
                # Forward input to agent
                current_agent_ws = _agent_connections.get(agent_id)
                if current_agent_ws:
                    try:
                        await current_agent_ws.send_text(json.dumps({
                            "type": "input",
                            "data": msg.get("data", ""),
                        }))
                    except Exception:
                        break
                else:
                    await ws.send_text(json.dumps({
                        "type": "session_ended",
                        "reason": "agent_disconnect",
                    }))
                    break

            elif msg_type == "resize":
                current_agent_ws = _agent_connections.get(agent_id)
                if current_agent_ws:
                    try:
                        await current_agent_ws.send_text(json.dumps({
                            "type": "resize",
                            "cols": msg.get("cols", 120),
                            "rows": msg.get("rows", 40),
                        }))
                    except Exception:
                        pass

            elif msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        logger.info("Browser disconnected from agent %s", agent.name)
    except Exception as e:
        logger.error("Browser WebSocket error: %s", e)
    finally:
        _browser_sessions.pop(agent_id, None)
        sid = _active_sessions.pop(agent_id, None)

        # Tell agent to end session
        current_agent_ws = _agent_connections.get(agent_id)
        if current_agent_ws:
            try:
                await current_agent_ws.send_text(json.dumps({"type": "session_end"}))
            except Exception:
                pass

        # Close audit session
        if sid:
            try:
                s = await TerminalSession.get(sid)
                if s:
                    s.ended_at = datetime.now(UTC)
                    await s.save()
            except Exception:
                pass
