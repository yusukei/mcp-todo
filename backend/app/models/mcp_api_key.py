from datetime import UTC, datetime

from beanie import Document, Indexed, Link
from pydantic import Field

from .user import User


class McpApiKey(Document):
    key_hash: Indexed(str, unique=True)
    name: str
    # ``project_scopes`` was removed: API keys now inherit access from their
    # owner's :class:`Project.members` membership instead of carrying their
    # own scope list. Existing documents with this field are ignored by
    # Beanie's pydantic model.
    created_by: Link[User]
    last_used_at: datetime | None = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "mcp_api_keys"
        indexes = [
            [("is_active", 1), ("created_at", -1)],
            [("created_by", 1), ("is_active", 1)],
        ]
