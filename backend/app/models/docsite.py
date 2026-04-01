from datetime import UTC, datetime

from beanie import Document, Indexed
from pydantic import BaseModel, Field


class DocSiteSection(BaseModel):
    """A node in the sidebar navigation tree."""

    title: str
    path: str | None = None  # None for group headers (non-link items)
    children: list["DocSiteSection"] = Field(default_factory=list)


class DocSite(Document):
    name: Indexed(str)  # type: ignore[valid-type]
    description: str = ""
    source_url: str = ""
    page_count: int = 0
    sections: list[DocSiteSection] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    async def save_updated(self, **kwargs):
        self.updated_at = datetime.now(UTC)
        await self.save(**kwargs)

    class Settings:
        name = "doc_sites"


class DocPage(Document):
    site_id: Indexed(str)  # type: ignore[valid-type]
    path: Indexed(str)  # type: ignore[valid-type]
    title: str = ""
    content: str = ""
    sort_order: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "doc_pages"
        indexes = [
            [("site_id", 1), ("path", 1)],
        ]
