from datetime import UTC, datetime

from beanie import Document, Indexed, Link
from pydantic import Field

from .user import User


class AllowedEmail(Document):
    email: Indexed(str, unique=True)
    created_by: Link[User] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "allowed_emails"
