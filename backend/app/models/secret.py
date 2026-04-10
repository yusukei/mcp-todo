from datetime import UTC, datetime

from beanie import Document, Indexed
from pymongo import IndexModel
from pydantic import Field


class ProjectSecret(Document):
    """Encrypted secret scoped to a project.

    The ``encrypted_value`` field stores a Fernet ciphertext (URL-safe
    base64 string) produced by :func:`app.core.crypto.encrypt`.  The
    plaintext is never persisted.

    The ``(project_id, key)`` pair is unique — enforced by a compound
    unique index so each project can have at most one secret per key
    name.
    """

    project_id: Indexed(str)  # type: ignore[valid-type]
    key: str  # e.g. "OPENAI_API_KEY"
    encrypted_value: str  # Fernet ciphertext (base64)
    description: str = ""  # human-readable memo (plaintext)
    created_by: str = ""  # "mcp:<key_name>" or "user:<user_id>"
    updated_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    async def save_updated(self, **kwargs):
        self.updated_at = datetime.now(UTC)
        await self.save(**kwargs)

    class Settings:
        name = "project_secrets"
        indexes = [
            IndexModel(
                [("project_id", 1), ("key", 1)],
                unique=True,
            ),
        ]


class SecretAccessLog(Document):
    """Audit log entry for secret operations.

    Records who accessed which secret and what operation was performed.
    Secret *values* are never stored in the log.
    """

    project_id: str
    secret_key: str  # the secret's key name, not its value
    operation: str  # "get" | "set" | "delete" | "inject"
    user_id: str
    auth_kind: str = ""  # "oauth" | "api_key"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "secret_access_logs"
        indexes = [
            [("project_id", 1), ("created_at", -1)],
        ]
