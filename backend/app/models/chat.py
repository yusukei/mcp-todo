"""Chat session and message models for Claude Code Web Chat."""

from datetime import UTC, datetime
from enum import StrEnum

from beanie import Document, Indexed
from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    idle = "idle"
    busy = "busy"


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class MessageStatus(StrEnum):
    streaming = "streaming"
    complete = "complete"
    error = "error"


class ToolCallData(BaseModel):
    """Tool invocation data within an assistant message."""
    tool_name: str
    input: dict = Field(default_factory=dict)
    output: str | None = None
    duration_ms: int | None = None


class ChatSession(Document):
    """A Claude Code chat session linked to a project."""
    project_id: Indexed(str)
    title: str = ""
    claude_session_id: str | None = None
    working_dir: str = ""
    status: SessionStatus = SessionStatus.idle
    model: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "chat_sessions"

    async def save_updated(self) -> "ChatSession":
        self.updated_at = datetime.now(UTC)
        await self.save()
        return self


class ChatMessage(Document):
    """A single message in a chat session."""
    session_id: Indexed(str)
    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCallData] = Field(default_factory=list)
    cost_usd: float | None = None
    duration_ms: int | None = None
    status: MessageStatus = MessageStatus.complete
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "chat_messages"
