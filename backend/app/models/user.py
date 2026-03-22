from datetime import UTC, datetime
from enum import StrEnum as str_enum

from beanie import Document, Indexed
from pydantic import Field


class AuthType(str_enum):
    admin = "admin"
    google = "google"


class User(Document):
    email: Indexed(str, unique=True)
    name: str
    auth_type: AuthType
    google_id: str | None = None
    password_hash: str | None = None
    is_active: bool = True
    is_admin: bool = False
    picture_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "users"

    async def save_updated(self) -> "User":
        self.updated_at = datetime.now(UTC)
        await self.save()
        return self
