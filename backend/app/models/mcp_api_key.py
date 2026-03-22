from datetime import UTC, datetime

from beanie import Document, Indexed, Link
from pydantic import Field

from .user import User


class McpApiKey(Document):
    key_hash: Indexed(str, unique=True)
    name: str
    project_scopes: list[str] = Field(default_factory=list)  # [] = all projects
    created_by: Link[User]
    last_used_at: datetime | None = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "mcp_api_keys"
