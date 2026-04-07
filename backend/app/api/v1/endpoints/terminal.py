"""Terminal remote access — Agent WebSocket + Workspace REST API.

Architecture:
- Agents connect via WebSocket and authenticate with first message
- MCP tools send commands to agents via AgentConnectionManager (request/response with Futures)
- RemoteWorkspace links projects to agents + directories
- All remote operations are logged to RemoteExecLog
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ....core.deps import get_admin_user
from ....core.security import hash_api_key
from ....models import User
from ....models.terminal import RemoteWorkspace, TerminalAgent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/terminal", tags=["terminal"])

# Server-side ping interval for dead connection detection
_PING_INTERVAL = 30  # seconds
_PING_TIMEOUT = 10  # seconds


# ── AgentConnectionManager ──────────────────────────────────


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

    def register(self, agent_id: str, ws: WebSocket) -> WebSocket | None:
        """Register a new connection, returning the old one if replaced."""
        old_ws = self._connections.get(agent_id)
        self._connections[agent_id] = ws
        if old_ws is not None and old_ws is not ws:
            logger.warning("Agent %s: replacing existing connection (reconnect)", agent_id)
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

    async def send_request(
        self, agent_id: str, msg_type: str, payload: dict, timeout: float = 60.0
    ) -> dict:
        """Send request to agent and await response via Future."""
        ws = self._connections.get(agent_id)
        if not ws:
            raise AgentOfflineError(agent_id)

        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = (agent_id, future)

        try:
            await ws.send_text(json.dumps({
                "type": msg_type, "request_id": request_id, **payload,
            }))
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result["error"])
            return result
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


# Module-level singleton
agent_manager = AgentConnectionManager()


async def reset_all_agents_online() -> int:
    """Reset all agents' is_online to False on server startup.

    Called from lifespan to clean up stale state from previous process.
    """
    result = await TerminalAgent.find(
        {"is_online": True}
    ).update({"$set": {"is_online": False}})
    count = result.modified_count if result else 0
    if count:
        logger.info("Reset %d stale agent online flags", count)
    return count


# ── Schemas ──────────────────────────────────────────────────


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class WorkspaceCreateRequest(BaseModel):
    agent_id: str
    project_id: str
    remote_path: str = Field(..., min_length=1, max_length=1000)
    label: str = Field("", max_length=200)


class WorkspaceUpdateRequest(BaseModel):
    remote_path: str | None = Field(None, min_length=1, max_length=1000)
    label: str | None = Field(None, max_length=200)


# ── Helpers ──────────────────────────────────────────────────


def _agent_dict(a: TerminalAgent) -> dict:
    agent_id = str(a.id)
    return {
        "id": agent_id,
        "name": a.name,
        "hostname": a.hostname,
        "os_type": a.os_type,
        "available_shells": a.available_shells,
        "is_online": agent_manager.is_connected(agent_id),
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        "created_at": a.created_at.isoformat(),
    }


def _build_workspace_dict(
    w: RemoteWorkspace,
    agent: TerminalAgent | None,
    project,  # Project | None — typed as Any to avoid a circular import
) -> dict:
    """Build a workspace response dict from already-fetched related entities.

    Synchronous on purpose so it can be reused inside batched loops without
    triggering extra DB round-trips.
    """
    return {
        "id": str(w.id),
        "agent_id": w.agent_id,
        "agent_name": agent.name if agent else "",
        "project_id": w.project_id,
        "project_name": project.name if project else "",
        "remote_path": w.remote_path,
        "label": w.label,
        "is_online": agent_manager.is_connected(w.agent_id),
        "created_at": w.created_at.isoformat(),
        "updated_at": w.updated_at.isoformat(),
    }


def _to_object_ids(ids: list[str]) -> list[ObjectId]:
    """Convert string IDs to ObjectId, silently dropping invalid ones."""
    out: list[ObjectId] = []
    for s in ids:
        try:
            out.append(ObjectId(s))
        except (InvalidId, TypeError):
            continue
    return out


async def _workspace_dict(w: RemoteWorkspace) -> dict:
    """Single-workspace variant: fetches the related agent and project.

    Used by create/update endpoints that operate on one workspace at a time.
    For list endpoints, use the batched path inside ``list_workspaces`` to
    avoid the 2N round-trips this helper would otherwise incur.
    """
    agent = await TerminalAgent.get(w.agent_id)
    from ....models import Project
    project = await Project.get(w.project_id)
    return _build_workspace_dict(w, agent, project)


# ── Health check ─────────────────────────────────────────────


@router.get("/health")
async def terminal_health() -> dict:
    return {"status": "ok", "websocket_endpoint": "/agent/ws"}


# ── Agent REST endpoints (admin only) ────────────────────────


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
    return {**_agent_dict(agent), "token": raw_token}


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: User = Depends(get_admin_user)) -> None:
    agent = await TerminalAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    agent_manager.unregister(agent_id)  # Force unregister (no ws check)
    await agent.delete()


# ── Workspace REST endpoints (admin only) ────────────────────


@router.get("/workspaces")
async def list_workspaces(user: User = Depends(get_admin_user)) -> list[dict]:
    """List all workspaces with their agent / project details.

    Performs at most three database queries regardless of workspace count:
      1. RemoteWorkspace.find_all()
      2. TerminalAgent.find({_id: {$in: [...]}})
      3. Project.find({_id: {$in: [...]}})

    Previously this used ``[await _workspace_dict(w) ...]`` which fired
    ``2 * N`` extra round-trips (1 agent + 1 project per workspace).
    """
    workspaces = await RemoteWorkspace.find_all().sort("-created_at").to_list()
    if not workspaces:
        return []

    # Collect unique foreign-key strings, preserve dedup so the $in queries
    # don't ship duplicates to MongoDB.
    agent_id_strs = {w.agent_id for w in workspaces if w.agent_id}
    project_id_strs = {w.project_id for w in workspaces if w.project_id}

    from ....models import Project

    agent_oids = _to_object_ids(list(agent_id_strs))
    project_oids = _to_object_ids(list(project_id_strs))

    agents_task = (
        TerminalAgent.find({"_id": {"$in": agent_oids}}).to_list()
        if agent_oids
        else asyncio.sleep(0, result=[])
    )
    projects_task = (
        Project.find({"_id": {"$in": project_oids}}).to_list()
        if project_oids
        else asyncio.sleep(0, result=[])
    )
    agents, projects = await asyncio.gather(agents_task, projects_task)

    agent_by_id: dict[str, TerminalAgent] = {str(a.id): a for a in agents}
    project_by_id: dict[str, object] = {str(p.id): p for p in projects}

    return [
        _build_workspace_dict(
            w,
            agent_by_id.get(w.agent_id),
            project_by_id.get(w.project_id),
        )
        for w in workspaces
    ]


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    # Validate agent exists and belongs to user
    agent = await TerminalAgent.get(body.agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")

    # Validate project exists
    from ....models import Project
    project = await Project.get(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check uniqueness (1 project = 1 workspace)
    existing = await RemoteWorkspace.find_one({"project_id": body.project_id})
    if existing:
        raise HTTPException(status_code=409, detail="Project already has a workspace")

    workspace = RemoteWorkspace(
        agent_id=body.agent_id,
        project_id=body.project_id,
        remote_path=body.remote_path,
        label=body.label,
    )
    await workspace.insert()
    # Reuse the already-fetched agent/project to avoid two extra round-trips.
    return _build_workspace_dict(workspace, agent, project)


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    workspace = await RemoteWorkspace.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if body.remote_path is not None:
        workspace.remote_path = body.remote_path
    if body.label is not None:
        workspace.label = body.label
    workspace.updated_at = datetime.now(UTC)
    await workspace.save()
    return await _workspace_dict(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(workspace_id: str, user: User = Depends(get_admin_user)) -> None:
    workspace = await RemoteWorkspace.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await workspace.delete()


# ── WebSocket: Agent ─────────────────────────────────────────


_RESPONSE_TYPES = frozenset({
    "exec_result", "file_content", "write_result", "dir_listing",
})


async def _server_ping_loop(ws: WebSocket, agent_id: str) -> None:
    """Send periodic pings to detect dead connections from the server side."""
    while True:
        await asyncio.sleep(_PING_INTERVAL)
        try:
            await asyncio.wait_for(
                ws.send_text(json.dumps({"type": "ping"})),
                timeout=_PING_TIMEOUT,
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
    agent = await TerminalAgent.find_one({"key_hash": key_hash})
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

            # Try to resolve as request/response first
            if msg_type in _RESPONSE_TYPES:
                agent_manager.resolve_request(msg)
                continue

            if msg_type == "agent_info":
                agent.hostname = msg.get("hostname", agent.hostname)
                agent.os_type = msg.get("os", agent.os_type)
                agent.available_shells = msg.get("shells", agent.available_shells)
                agent.last_seen_at = datetime.now(UTC)
                await agent.save()

            elif msg_type == "pong":
                pass

            elif msg_type in ("chat_event", "chat_complete", "chat_error"):
                from .chat import handle_chat_event
                asyncio.ensure_future(handle_chat_event(msg))

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
