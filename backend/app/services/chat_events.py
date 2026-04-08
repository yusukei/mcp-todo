"""Chat event/dispatch services.

Extracted from `api/v1/endpoints/chat.py` so the agent WebSocket handler
in `endpoints/workspaces/` and the lifespan recovery hook in `app/main.py`
can import them without pulling in the FastAPI router module.

Three responsibility groups live here:

1. **Outbound dispatch** (`dispatch_to_agent`, `cancel_agent_task`)
   — push chat user messages and cancel signals to the connected agent
2. **Inbound event handling** (`handle_chat_event`, `_process_stream_event`)
   — receive `chat_event` / `chat_complete` / `chat_error` from agents
   and rebroadcast them to all attached browsers
3. **State recovery** (`recover_stale_sessions`, `complete_with_error`)
   — drain `busy` sessions left over after agent disconnects or errors
"""

from __future__ import annotations

import logging
import uuid

from ..models.chat import (
    ChatMessage,
    ChatSession,
    MessageRole,
    MessageStatus,
    SessionStatus,
)
from ..models.remote import RemoteWorkspace
from .agent_manager import AgentOfflineError, agent_manager
from .chat_manager import chat_manager

logger = logging.getLogger(__name__)


def message_dict(m: ChatMessage) -> dict:
    """Serialize a ChatMessage for browser broadcast / REST responses."""
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


# ── Outbound: dispatch to agent ──────────────────────────────


async def dispatch_to_agent(session: ChatSession, content: str) -> None:
    """Send a chat message to the Agent for Claude Code processing.

    The agent will spawn `claude` CLI and stream events back. Events are
    handled by `handle_chat_event()` which broadcasts to all browsers.
    """
    workspace = await RemoteWorkspace.find_one({"project_id": session.project_id})
    if not workspace:
        await complete_with_error(session, "No workspace configured for this project")
        return

    agent_id = workspace.agent_id
    if not agent_manager.is_connected(agent_id):
        await complete_with_error(session, "Agent is offline")
        return

    request_id = uuid.uuid4().hex[:12]

    try:
        await agent_manager.send_raw(agent_id, {
            "type": "chat_message",
            "request_id": request_id,
            "session_id": str(session.id),
            "content": content,
            "claude_session_id": session.claude_session_id,
            "working_dir": session.working_dir,
            "model": session.model,
        })
    except AgentOfflineError:
        await complete_with_error(session, "Agent connection lost")
    except Exception as e:
        logger.error("Failed to dispatch to agent: %s", e)
        await complete_with_error(session, f"Failed to send to agent: {e}")


async def cancel_agent_task(session: ChatSession) -> None:
    """Send cancel request to the Agent."""
    workspace = await RemoteWorkspace.find_one({"project_id": session.project_id})
    if not workspace:
        return

    agent_id = workspace.agent_id
    try:
        await agent_manager.send_raw(agent_id, {
            "type": "chat_cancel",
            "session_id": str(session.id),
        })
    except AgentOfflineError:
        pass
    except Exception as e:
        logger.warning("Failed to send cancel: %s", e)


# ── State recovery ───────────────────────────────────────────


async def complete_with_error(session: ChatSession, error: str) -> None:
    """Insert a system error message and reset the session to idle."""
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


async def recover_stale_sessions() -> int:
    """Reset sessions stuck in 'busy' state (e.g. after agent disconnect).

    Called from the application lifespan and whenever a browser reconnects
    to detect orphaned sessions left over by a crashed/restarted process.
    """
    count = 0
    busy_sessions = await ChatSession.find({"status": "busy"}).to_list()
    for session in busy_sessions:
        workspace = await RemoteWorkspace.find_one({"project_id": session.project_id})
        agent_online = workspace and agent_manager.is_connected(workspace.agent_id)

        if not agent_online:
            session.status = SessionStatus.idle
            await session.save_updated()

            # Mark any streaming message as error
            streaming = await ChatMessage.find_one({
                "session_id": str(session.id),
                "status": MessageStatus.streaming,
            })
            if streaming:
                streaming.status = MessageStatus.error
                streaming.content += "\n\n[Agent disconnected]"
                await streaming.save()

            count += 1
            logger.info("Recovered stale busy session: %s", session.id)
    return count


# ── Inbound: agent → browser event handling ──────────────────


async def handle_chat_event(msg: dict) -> None:
    """Process a chat event from the Agent and broadcast to all browsers.

    Called from the agent WebSocket message loop in `endpoints/workspaces/websocket.py`.
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
            await complete_with_error(session, msg.get("error", "Unknown error"))


async def _process_stream_event(session_id: str, event: dict) -> None:
    """Parse a single stream-json event from the claude CLI and broadcast."""
    event_type = event.get("type", "")

    # Text content delta
    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            text = delta.get("text", "")

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

    # NOTE: content_block_delta input_json_delta is unreachable today
    # because the earlier `if event_type == "content_block_delta"` branch
    # consumes the same event type. Kept here intentionally so the
    # behaviour matches the pre-refactor file; cleanup is a separate task.
    elif event_type == "content_block_delta":  # pragma: no cover
        delta = event.get("delta", {})
        if delta.get("type") == "input_json_delta":
            await chat_manager.broadcast(session_id, {
                "type": "tool_input_delta",
                "partial_json": delta.get("partial_json", ""),
            })

    elif event_type == "tool_result":
        content = event.get("content", "")
        tool_use_id = event.get("tool_use_id", "")

        streaming_msg = await ChatMessage.find_one({
            "session_id": session_id,
            "role": MessageRole.assistant,
            "status": MessageStatus.streaming,
        })
        if streaming_msg:
            from ..models.chat import ToolCallData
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

    elif event_type == "result":
        # Final summary handled by handle_chat_event via chat_complete message
        pass
