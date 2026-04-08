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
from ..models.project import Project
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
    project = await Project.get(session.project_id)
    if not project or not project.remote:
        await complete_with_error(session, "No remote agent bound to this project")
        return

    agent_id = project.remote.agent_id
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
    project = await Project.get(session.project_id)
    if not project or not project.remote:
        return

    agent_id = project.remote.agent_id
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
        project = await Project.get(session.project_id)
        agent_online = (
            project is not None
            and project.remote is not None
            and agent_manager.is_connected(project.remote.agent_id)
        )

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


async def _ensure_streaming_message(session_id: str) -> ChatMessage:
    """Find or create the in-progress assistant message for this session."""
    streaming_msg = await ChatMessage.find_one({
        "session_id": session_id,
        "role": MessageRole.assistant,
        "status": MessageStatus.streaming,
    })
    if streaming_msg:
        return streaming_msg

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
    return streaming_msg


async def _process_stream_event(session_id: str, event: dict) -> None:
    """Parse a single stream-json event from the claude CLI and broadcast.

    The claude CLI v2 emits SDKMessage-shaped events when run with
    `--output-format=stream-json --verbose`:

        {"type":"system","subtype":"init", ...}
        {"type":"assistant","message":{"content":[{"type":"text","text":"..."},
                                                  {"type":"tool_use","name":"...","input":{...}}]}}
        {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"...","content":"..."}]}}
        {"type":"result", ...}

    Each `assistant` event represents a *complete* assistant turn (it is not
    a delta). When claude needs multiple turns to satisfy a request — e.g.
    text → tool_use → tool_result → text — claude emits one `assistant`
    event per turn. We append text/tool blocks to the same in-progress
    streaming ChatMessage so the browser sees a single assistant bubble
    that grows over time, then `chat_complete` finalises it.
    """
    event_type = event.get("type", "")

    if event_type == "assistant":
        from ..models.chat import ToolCallData

        message = event.get("message", {}) or {}
        content_blocks = message.get("content", []) or []

        streaming_msg = await _ensure_streaming_message(session_id)

        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text", "") or ""
                if not text:
                    continue
                streaming_msg.content += text
                await chat_manager.broadcast(session_id, {
                    "type": "text_delta",
                    "message_id": str(streaming_msg.id),
                    "text": text,
                })

            elif block_type == "tool_use":
                tool_name = block.get("name", "") or ""
                tool_input = block.get("input", {}) or {}
                tool_use_id = block.get("id", "") or ""
                streaming_msg.tool_calls.append(ToolCallData(
                    tool_name=tool_name,
                    input=tool_input,
                ))
                await chat_manager.broadcast(session_id, {
                    "type": "tool_use",
                    "message_id": str(streaming_msg.id),
                    "tool": tool_name,
                    "tool_use_id": tool_use_id,
                    "input": tool_input,
                })

        await streaming_msg.save()

    elif event_type == "user":
        # SDKUserMessage typically wraps tool_result blocks emitted after
        # claude executed a tool. There is no user-facing text in this
        # event — it just carries tool outputs back into the transcript.
        message = event.get("message", {}) or {}
        content_blocks = message.get("content", []) or []
        for block in content_blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue

            raw_content = block.get("content", "")
            if isinstance(raw_content, list):
                # Tool results may be a list of typed parts; flatten text parts.
                text_parts = []
                for part in raw_content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content_str = "\n".join(text_parts)
            elif isinstance(raw_content, str):
                content_str = raw_content
            else:
                content_str = str(raw_content)
            content_str = content_str[:5000]

            tool_use_id = block.get("tool_use_id", "") or ""

            streaming_msg = await ChatMessage.find_one({
                "session_id": session_id,
                "role": MessageRole.assistant,
                "status": MessageStatus.streaming,
            })
            if streaming_msg:
                # Attach the output to the most recent matching tool_call
                # whose output is still empty.
                for tc in reversed(streaming_msg.tool_calls):
                    if not tc.output:
                        tc.output = content_str
                        break
                await streaming_msg.save()

            await chat_manager.broadcast(session_id, {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "output": content_str,
            })

    elif event_type in ("system", "rate_limit_event", "result"):
        # system: init metadata — no UI work needed.
        # rate_limit_event: informational; intentionally ignored.
        # result: finalisation is performed by handle_chat_event via the
        #         chat_complete envelope sent by the agent.
        return
