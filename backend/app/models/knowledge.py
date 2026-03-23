from datetime import UTC, datetime
from enum import StrEnum as str_enum

from beanie import Document, Indexed
from pydantic import Field


class KnowledgeCategory(str_enum):
    recipe = "recipe"
    reference = "reference"
    tip = "tip"
    troubleshooting = "troubleshooting"
    architecture = "architecture"


class Knowledge(Document):
    title: Indexed(str)  # type: ignore[valid-type]
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    category: KnowledgeCategory = KnowledgeCategory.reference
    source: str | None = None
    created_by: str = ""
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    async def save_updated(self, **kwargs):
        self.updated_at = datetime.now(UTC)
        await self.save(**kwargs)

    class Settings:
        name = "knowledge"
        indexes = [
            [("is_deleted", 1), ("tags", 1)],
            [("category", 1), ("is_deleted", 1)],
        ]
