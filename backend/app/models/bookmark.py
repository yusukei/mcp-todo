from datetime import UTC, datetime
from enum import StrEnum as str_enum

from beanie import Document, Indexed
from pydantic import BaseModel, Field


class ClipStatus(str_enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class BookmarkMetadata(BaseModel):
    meta_title: str = ""
    meta_description: str = ""
    favicon_url: str = ""
    og_image_url: str = ""
    site_name: str = ""
    author: str = ""
    published_date: str | None = None


class BookmarkCollection(Document):
    project_id: Indexed(str)  # type: ignore[valid-type]
    name: Indexed(str)  # type: ignore[valid-type]
    description: str = ""
    icon: str = "folder"
    color: str = "#6366f1"
    sort_order: int = 0
    created_by: str = ""
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    async def save_updated(self, **kwargs):
        self.updated_at = datetime.now(UTC)
        await self.save(**kwargs)

    class Settings:
        name = "bookmark_collections"
        indexes = [
            [("project_id", 1), ("is_deleted", 1), ("sort_order", 1)],
        ]


class Bookmark(Document):
    project_id: Indexed(str)  # type: ignore[valid-type]
    url: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    collection_id: str | None = None
    metadata: BookmarkMetadata = Field(default_factory=BookmarkMetadata)

    # Clipping
    clip_status: ClipStatus = ClipStatus.pending
    clip_content: str = ""
    clip_markdown: str = ""
    clip_error: str = ""
    thumbnail_path: str = ""
    local_images: dict[str, str] = Field(default_factory=dict)

    is_starred: bool = False
    sort_order: int = 0
    created_by: str = ""
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    async def save_updated(self, **kwargs):
        self.updated_at = datetime.now(UTC)
        await self.save(**kwargs)

    class Settings:
        name = "bookmarks"
        indexes = [
            [("project_id", 1), ("is_deleted", 1), ("sort_order", 1)],
            [("project_id", 1), ("collection_id", 1), ("is_deleted", 1)],
            [("project_id", 1), ("is_deleted", 1), ("tags", 1)],
            [("url", 1), ("project_id", 1)],
            [("clip_status", 1), ("is_deleted", 1)],
            [("is_starred", 1), ("project_id", 1), ("is_deleted", 1)],
        ]
