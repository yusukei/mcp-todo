"""Chat session REST API + WebSocket endpoint for Claude Code Web Chat."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....models import Project, User
from ....models.chat import ChatMessage, ChatSession, MessageRole, MessageStatus, SessionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ── Schemas ──────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    project_id: str
    title: str = Field("", max_length=255)
    model: str = Field("", max_length=100)


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(None, max_length=255)
    model: str | None = Field(None, max_length=100)


# ── Helpers ──────────────────────────────────────────────────


async def _check_project_access(project_id: str, user: User) -> Project:
    """Validate project exists and user has access."""
    project = await Project.get(project_id)
    if not project or not project.has_member(str(user.id)):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _session_dict(s: ChatSession) -> dict:
    return {
        "id": str(s.id),
        "project_id": s.project_id,
        "title": s.title,
        "claude_session_id": s.claude_session_id,
        "working_dir": s.working_dir,
        "status": s.status,
        "model": s.model,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _message_dict(m: ChatMessage) -> dict:
    return {
        "id": str(m.id),
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content,
        "tool_calls": [tc.model_dump() for tc in m.tool_calls],
        "cost_usd": m.cost_usd,
        "duration_ms": m.duration_ms,
        "status": m.status,
        "created_at": m.created_at.isoformat(),
    }


# ── Session CRUD ─────────────────────────────────────────────


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    user: User = Depends(get_current_user),
) -> dict:
    project = await _check_project_access(body.project_id, user)

    # Resolve working_dir from RemoteWorkspace
    from ....models.terminal import RemoteWorkspace
    workspace = await RemoteWorkspace.find_one({"project_id": body.project_id})
    working_dir = workspace.remote_path if workspace else ""

    session = ChatSession(
        project_id=body.project_id,
        title=body.title or f"Chat {datetime.now(UTC).strftime('%m/%d %H:%M')}",
        working_dir=working_dir,
        model=body.model,
        created_by=str(user.id),
    )
    await session.insert()
    return _session_dict(session)


@router.get("/sessions")
async def list_sessions(
    project_id: str | None = None,
    user: User = Depends(get_current_user),
) -> list[dict]:
    query: dict = {}
    if project_id:
        await _check_project_access(project_id, user)
        query["project_id"] = project_id

    sessions = await ChatSession.find(query).sort("-updated_at").to_list()
    return [_session_dict(s) for s in sessions]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    session = await ChatSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await _check_project_access(session.project_id, user)
    return _session_dict(session)


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    user: User = Depends(get_current_user),
) -> dict:
    session = await ChatSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await _check_project_access(session.project_id, user)

    if body.title is not None:
        session.title = body.title
    if body.model is not None:
        session.model = body.model
    await session.save_updated()
    return _session_dict(session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
) -> None:
    session = await ChatSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await _check_project_access(session.project_id, user)

    # Delete all messages in this session
    await ChatMessage.find({"session_id": str(session.id)}).delete()
    await session.delete()


# ── Messages ─────────────────────────────────────────────────


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    limit: int = 100,
    skip: int = 0,
    user: User = Depends(get_current_user),
) -> dict:
    session = await ChatSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await _check_project_access(session.project_id, user)

    query = ChatMessage.find({"session_id": str(session.id)})
    total = await query.count()
    messages = await query.sort("created_at").skip(skip).limit(limit).to_list()

    return {
        "items": [_message_dict(m) for m in messages],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


# ── WebSocket ────────────────────────────────────────────────
# Phase 3 で実装（マルチブラウザファンアウト + Agent連携）


class ChatConnectionManager:
    """Manages WebSocket connections per chat session for multi-browser fan-out."""

    def __init__(self) -> None:
        # session_id → set of WebSocket connections
        self._connections: dict[str, set[WebSocket]] = {}

    def connect(self, session_id: str, ws: WebSocket) -> None:
        if session_id not in self._connections:
            self._connections[session_id] = set()
        self._connections[session_id].add(ws)

    def disconnect(self, session_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(session_id)
        if conns:
            conns.discard(ws)
            if not conns:
                del self._connections[session_id]

    async def broadcast(self, session_id: str, message: dict) -> None:
        """Send message to all browsers connected to this session."""
        conns = self._connections.get(session_id, set())
        payload = json.dumps(message, default=str)
        disconnected = []
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            conns.discard(ws)

    def get_session_ids(self) -> list[str]:
        return list(self._connections.keys())

    def connection_count(self, session_id: str) -> int:
        return len(self._connections.get(session_id, set()))


# Module-level singleton
chat_manager = ChatConnectionManager()


@router.websocket("/ws/{session_id}")
async def chat_websocket(ws: WebSocket, session_id: str):
    """WebSocket endpoint for real-time chat with Claude Code.

    Supports multi-browser fan-out: all browsers connected to the same
    session receive the same events.
    """
    await ws.accept()

    # Validate session exists
    session = await ChatSession.get(session_id)
    if not session:
        await ws.close(code=4004, reason="Session not found")
        return

    chat_manager.connect(session_id, ws)
    logger.info("Chat WS connected: session=%s (total=%d)", session_id, chat_manager.connection_count(session_id))

    # Send current session status
    await ws.send_text(json.dumps({
        "type": "status",
        "session_status": session.status,
    }))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "send_message":
                content = msg.get("content", "").strip()
                if not content:
                    continue
                if session.status == SessionStatus.busy:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "detail": "Session is busy. Wait for the current response to complete.",
                    }))
                    continue

                # Save user message
                user_msg = ChatMessage(
                    session_id=str(session.id),
                    role=MessageRole.user,
                    content=content,
                )
                await user_msg.insert()

                # Broadcast user message to all browsers
                await chat_manager.broadcast(session_id, {
                    "type": "user_message",
                    "message": _message_dict(user_msg),
                })

                # Mark session busy
                session.status = SessionStatus.busy
                await session.save_updated()
                await chat_manager.broadcast(session_id, {
                    "type": "status",
                    "session_status": "busy",
                })

                # Dispatch to Agent (Phase 2/3)
                await _dispatch_to_agent(session, content)

            elif msg_type == "cancel":
                await _cancel_agent_task(session)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Chat WS error: session=%s, error=%s", session_id, e)
    finally:
        chat_manager.disconnect(session_id, ws)
        logger.info("Chat WS disconnected: session=%s (remaining=%d)", session_id, chat_manager.connection_count(session_id))


async def _dispatch_to_agent(session: ChatSession, content: str) -> None:
    """Send a chat message to the Agent for Claude Code processing.

    The agent will spawn `claude` CLI and stream events back.
    Events are handled by handle_chat_event() which broadcasts to all browsers.
    """
    from .terminal import agent_manager, AgentOfflineError

    # Find agent for this project's workspace
    from ....models.terminal import RemoteWorkspace
    workspace = await RemoteWorkspace.find_one({"project_id": session.project_id})
    if not workspace:
        await _complete_with_error(session, "No workspace configured for this project")
        return

    agent_id = workspace.agent_id
    if not agent_manager.is_connected(agent_id):
        await _complete_with_error(session, "Agent is offline")
        return

    import uuid
    request_id = uuid.uuid4().hex[:12]

    try:
        ws = agent_manager._connections.get(agent_id)
        if not ws:
            await _complete_with_error(session, "Agent connection lost")
            return

        await ws.send_text(json.dumps({
            "type": "chat_message",
            "request_id": request_id,
            "session_id": str(session.id),
            "content": content,
            "claude_session_id": session.claude_session_id,
            "working_dir": session.working_dir,
            "model": session.model,
        }))
    except Exception as e:
        logger.error("Failed to dispatch to agent: %s", e)
        await _complete_with_error(session, f"Failed to send to agent: {e}")


async def _cancel_agent_task(session: ChatSession) -> None:
    """Send cancel request to the Agent."""
    from .terminal import agent_manager
    from ....models.terminal import RemoteWorkspace

    workspace = await RemoteWorkspace.find_one({"project_id": session.project_id})
    if not workspace:
        return

    agent_id = workspace.agent_id
    ws = agent_manager._connections.get(agent_id)
    if ws:
        try:
            await ws.send_text(json.dumps({
                "type": "chat_cancel",
                "session_id": str(session.id),
            }))
        except Exception as e:
            logger.warning("Failed to send cancel: %s", e)


async def _complete_with_error(session: ChatSession, error: str) -> None:
    """Complete a failed message and reset session to idle."""
    error_msg = ChatMessage(
        session_id=str(session.id),
        role=MessageRole.system,
        content=error,
        status=MessageStatus.error,
    )
    await error_msg.insert()

    session.status = SessionStatus.idle
    await session.save_updated()

    session_id = str(session.id)
    await chat_manager.broadcast(session_id, {
        "type": "error",
        "detail": error,
    })
    await chat_manager.broadcast(session_id, {
        "type": "status",
        "session_status": "idle",
    })


# ── Agent event handler (called from terminal.py agent WS loop) ──


async def handle_chat_event(msg: dict) -> None:
    """Process a chat event from the Agent and broadcast to all browsers.

    Called from the agent WebSocket message loop in terminal.py.
    """
    session_id = msg.get("session_id")
    if not session_id:
        return

    msg_type = msg.get("type")

    if msg_type == "chat_event":
        event = msg.get("event", {})
        await _process_stream_event(session_id, event)

    elif msg_type == "chat_complete":
        session = await ChatSession.get(session_id)
        if session:
            session.claude_session_id = msg.get("claude_session_id")
            session.status = SessionStatus.idle
            await session.save_updated()

        # Finalize the streaming assistant message
        streaming_msg = await ChatMessage.find_one({
            "session_id": session_id,
            "role": MessageRole.assistant,
            "status": MessageStatus.streaming,
        })
        if streaming_msg:
            streaming_msg.status = MessageStatus.complete
            streaming_msg.cost_usd = msg.get("cost_usd")
            streaming_msg.duration_ms = msg.get("duration_ms")
            await streaming_msg.save()

            await chat_manager.broadcast(session_id, {
                "type": "assistant_end",
                "message_id": str(streaming_msg.id),
                "cost_usd": msg.get("cost_usd"),
                "duration_ms": msg.get("duration_ms"),
            })

        await chat_manager.broadcast(session_id, {
            "type": "status",
            "session_status": "idle",
        })

    elif msg_type == "chat_error":
        session = await ChatSession.get(session_id)
        if session:
            await _complete_with_error(session, msg.get("error", "Unknown error"))


async def _process_stream_event(session_id: str, event: dict) -> None:
    """Parse a single stream-json event from claude CLI and broadcast."""
    event_type = event.get("type", "")

    # Text content delta
    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            text = delta.get("text", "")

            # Ensure assistant message exists (create on first delta)
            streaming_msg = await ChatMessage.find_one({
                "session_id": session_id,
                "role": MessageRole.assistant,
                "status": MessageStatus.streaming,
            })
            if not streaming_msg:
                streaming_msg = ChatMessage(
                    session_id=session_id,
                    role=MessageRole.assistant,
                    status=MessageStatus.streaming,
                )
                await streaming_msg.insert()
                await chat_manager.broadcast(session_id, {
                    "type": "assistant_start",
                    "message_id": str(streaming_msg.id),
                })

            streaming_msg.content += text
            await streaming_msg.save()

            await chat_manager.broadcast(session_id, {
                "type": "text_delta",
                "message_id": str(streaming_msg.id),
                "text": text,
            })

    # Tool use start
    elif event_type == "content_block_start":
        block = event.get("content_block", {})
        if block.get("type") == "tool_use":
            await chat_manager.broadcast(session_id, {
                "type": "tool_use",
                "message_id": "",
                "tool": block.get("name", ""),
                "tool_use_id": block.get("id", ""),
                "input": block.get("input", {}),
            })

    # Tool use input delta (accumulate JSON)
    elif event_type == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "input_json_delta":
            await chat_manager.broadcast(session_id, {
                "type": "tool_input_delta",
                "partial_json": delta.get("partial_json", ""),
            })

    # Tool result
    elif event_type == "tool_result":
        content = event.get("content", "")
        tool_use_id = event.get("tool_use_id", "")

        # Append to streaming message's tool_calls
        streaming_msg = await ChatMessage.find_one({
            "session_id": session_id,
            "role": MessageRole.assistant,
            "status": MessageStatus.streaming,
        })
        if streaming_msg:
            from ....models.chat import ToolCallData
            streaming_msg.tool_calls.append(ToolCallData(
                tool_name=tool_use_id,
                output=content[:5000] if isinstance(content, str) else str(content)[:5000],
            ))
            await streaming_msg.save()

        await chat_manager.broadcast(session_id, {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "output": content[:5000] if isinstance(content, str) else str(content)[:5000],
        })

    # Result (final summary — handled by chat_complete)
    elif event_type == "result":
        pass  # Handled in handle_chat_event via chat_complete message
