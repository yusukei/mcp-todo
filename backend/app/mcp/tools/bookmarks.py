import asyncio
import logging

from fastmcp.exceptions import ToolError

from ...models.bookmark import Bookmark, BookmarkCollection, ClipStatus
from ...services.bookmark_search import index_bookmark, deindex_bookmark
from ...services.serializers import (
    bookmark_collection_to_dict as _coll_dict,
    bookmark_summary as _bookmark_summary,
    bookmark_to_dict as _bookmark_dict,
)
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _check_project_not_locked, _resolve_project_id

logger = logging.getLogger(__name__)


# ── Bookmark Collection Tools ────────────────────────────────


@mcp.tool()
async def create_bookmark_collection(
    project_id: str,
    name: str,
    description: str = "",
    icon: str = "folder",
    color: str = "#6366f1",
) -> dict:
    """Create a bookmark collection (folder) in a project.

    Args:
        project_id: Project ID or project name
        name: Collection name (max 255 chars)
        description: Collection description
        icon: Lucide icon name (default: folder)
        color: Hex color code (default: #6366f1)
    """
    if not name or not name.strip():
        raise ToolError("Name is required")
    if len(name) > 255:
        raise ToolError("Name exceeds 255 characters")

    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])
    await _check_project_not_locked(project_id)

    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    c = BookmarkCollection(
        project_id=project_id,
        name=name.strip(),
        description=description,
        icon=icon,
        color=color,
        created_by=creator,
    )
    await c.insert()
    return _coll_dict(c)


@mcp.tool()
async def list_bookmark_collections(project_id: str) -> dict:
    """List all bookmark collections in a project.

    Args:
        project_id: Project ID or project name
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    items = (
        await BookmarkCollection.find(
            {"project_id": project_id, "is_deleted": False},
        )
        .sort("+sort_order", "+name")
        .to_list()
    )
    return {"items": [_coll_dict(c) for c in items], "total": len(items)}


@mcp.tool()
async def update_bookmark_collection(
    collection_id: str,
    name: str | None = None,
    description: str | None = None,
    icon: str | None = None,
    color: str | None = None,
) -> dict:
    """Update a bookmark collection.

    Args:
        collection_id: Collection ID
        name: New name
        description: New description
        icon: New Lucide icon name
        color: New hex color
    """
    key_info = await authenticate()

    c = await BookmarkCollection.get(collection_id)
    if not c or c.is_deleted:
        raise ToolError(f"Collection not found: {collection_id}")

    check_project_access(c.project_id, key_info["project_scopes"])
    await _check_project_not_locked(c.project_id)

    if name is not None:
        c.name = name.strip()
    if description is not None:
        c.description = description
    if icon is not None:
        c.icon = icon
    if color is not None:
        c.color = color

    await c.save_updated()
    return _coll_dict(c)


@mcp.tool()
async def delete_bookmark_collection(collection_id: str) -> dict:
    """Delete a bookmark collection. Bookmarks in the collection become uncategorized.

    Args:
        collection_id: Collection ID
    """
    key_info = await authenticate()

    c = await BookmarkCollection.get(collection_id)
    if not c or c.is_deleted:
        raise ToolError(f"Collection not found: {collection_id}")

    check_project_access(c.project_id, key_info["project_scopes"])
    await _check_project_not_locked(c.project_id)

    c.is_deleted = True
    await c.save_updated()

    # Unset collection_id on bookmarks
    await Bookmark.find(
        {"collection_id": collection_id, "is_deleted": False},
    ).update({"$set": {"collection_id": None}})

    return {"deleted": True, "id": collection_id}


# ── Bookmark Tools ───────────────────────────────────────────


@mcp.tool()
async def create_bookmark(
    project_id: str,
    url: str,
    title: str = "",
    description: str = "",
    tags: list[str] | None = None,
    collection_id: str | None = None,
) -> dict:
    """Create a bookmark. Web clipping starts automatically in the background.

    Args:
        project_id: Project ID or project name
        url: URL to bookmark
        title: Bookmark title (auto-fetched if empty)
        description: Description
        tags: Tags for categorization
        collection_id: Collection ID to place the bookmark in
    """
    if not url or not url.strip():
        raise ToolError("URL is required")
    if len(url) > 2048:
        raise ToolError("URL exceeds 2048 characters")

    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])
    await _check_project_not_locked(project_id)

    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"
    normalized_tags = [t.strip().lower() for t in (tags or []) if t.strip()]

    bm = Bookmark(
        project_id=project_id,
        url=url.strip(),
        title=title.strip() if title.strip() else url.strip(),
        description=description,
        tags=normalized_tags,
        collection_id=collection_id,
        clip_status=ClipStatus.pending,
        created_by=creator,
    )
    await bm.insert()

    # Launch background clipping
    asyncio.create_task(_run_clip_bg(str(bm.id)))

    return _bookmark_dict(bm)


@mcp.tool()
async def get_bookmark(bookmark_id: str) -> dict:
    """Get a bookmark by ID, including clipped content.

    Args:
        bookmark_id: Bookmark ID
    """
    key_info = await authenticate()

    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted:
        raise ToolError(f"Bookmark not found: {bookmark_id}")

    check_project_access(bm.project_id, key_info["project_scopes"])
    return _bookmark_dict(bm)


@mcp.tool()
async def update_bookmark(
    bookmark_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    collection_id: str | None = None,
    is_starred: bool | None = None,
) -> dict:
    """Update a bookmark. Only provided fields are changed.

    Args:
        bookmark_id: Bookmark ID
        title: New title
        description: New description
        tags: New tags (replaces all)
        collection_id: New collection ID (empty string to unset)
        is_starred: Star/unstar
    """
    key_info = await authenticate()

    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted:
        raise ToolError(f"Bookmark not found: {bookmark_id}")

    check_project_access(bm.project_id, key_info["project_scopes"])
    await _check_project_not_locked(bm.project_id)

    if title is not None:
        bm.title = title.strip()
    if description is not None:
        bm.description = description
    if tags is not None:
        bm.tags = [t.strip().lower() for t in tags if t.strip()]
    if collection_id is not None:
        bm.collection_id = collection_id if collection_id != "" else None
    if is_starred is not None:
        bm.is_starred = is_starred

    await bm.save_updated()
    await index_bookmark(bm)
    return _bookmark_dict(bm)


@mcp.tool()
async def delete_bookmark(bookmark_id: str) -> dict:
    """Delete a bookmark (soft delete).

    Args:
        bookmark_id: Bookmark ID
    """
    key_info = await authenticate()

    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted:
        raise ToolError(f"Bookmark not found: {bookmark_id}")

    check_project_access(bm.project_id, key_info["project_scopes"])
    await _check_project_not_locked(bm.project_id)

    bm.is_deleted = True
    await bm.save_updated()
    await deindex_bookmark(bookmark_id)

    return {"deleted": True, "id": bookmark_id}


@mcp.tool()
async def list_bookmarks(
    project_id: str,
    collection_id: str | None = None,
    tag: str | None = None,
    starred: bool | None = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List bookmarks in a project with optional filters.

    Args:
        project_id: Project ID or project name
        collection_id: Filter by collection (empty string for uncategorized)
        tag: Filter by tag
        starred: Filter starred bookmarks only
        limit: Max results (default 50, max 200)
        skip: Offset for pagination
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    check_project_access(project_id, key_info["project_scopes"])

    limit = min(limit, 200)
    filters: dict = {"project_id": project_id, "is_deleted": False}
    if collection_id is not None:
        filters["collection_id"] = collection_id if collection_id != "" else None
    if tag:
        filters["tags"] = tag.lower()
    if starred is not None:
        filters["is_starred"] = starred

    total = await Bookmark.find(filters).count()
    items = (
        await Bookmark.find(filters)
        .skip(skip)
        .limit(limit)
        .sort("+sort_order", "-updated_at")
        .to_list()
    )
    return {
        "items": [_bookmark_summary(b) for b in items],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@mcp.tool()
async def search_bookmarks(
    query: str,
    project_id: str | None = None,
    limit: int = 20,
) -> dict:
    """Full-text search across bookmarks (title, description, tags, URL, clipped content).

    Uses Tantivy with Japanese morphological analysis (Lindera).
    Falls back to MongoDB regex if Tantivy is not available.

    Args:
        query: Search query
        project_id: Limit to a specific project (ID or name)
        limit: Max results (default 20)
    """
    if not query or not query.strip():
        raise ToolError("Query is required")

    key_info = await authenticate()
    if project_id:
        project_id = await _resolve_project_id(project_id)
        check_project_access(project_id, key_info["project_scopes"])

    from ...services.bookmark_search import BookmarkSearchService

    svc = BookmarkSearchService.get_instance()
    if svc:
        result = svc.search(query, project_id=project_id, limit=limit)
        if result.results:
            bm_ids = [r["bookmark_id"] for r in result.results]
            bookmarks = await Bookmark.find(
                {"_id": {"$in": [__import__("bson").ObjectId(bid) for bid in bm_ids]}, "is_deleted": False},
            ).to_list()
            bm_map = {str(b.id): b for b in bookmarks}
            items = [_bookmark_summary(bm_map[r["bookmark_id"]]) for r in result.results if r["bookmark_id"] in bm_map]
            return {"items": items, "total": result.total}

    # Fallback: MongoDB regex search
    import re as _re
    pattern = _re.escape(query.strip())
    filters: dict = {"is_deleted": False}
    if project_id:
        filters["project_id"] = project_id

    scopes = key_info["project_scopes"]
    if scopes:
        filters["project_id"] = {"$in": scopes}

    filters["$or"] = [
        {"title": {"$regex": pattern, "$options": "i"}},
        {"url": {"$regex": pattern, "$options": "i"}},
        {"description": {"$regex": pattern, "$options": "i"}},
        {"tags": {"$regex": pattern, "$options": "i"}},
        {"clip_markdown": {"$regex": pattern, "$options": "i"}},
    ]

    total = await Bookmark.find(filters).count()
    items = await Bookmark.find(filters).limit(limit).sort("-updated_at").to_list()
    return {"items": [_bookmark_summary(b) for b in items], "total": total}


@mcp.tool()
async def clip_bookmark(bookmark_id: str) -> dict:
    """Trigger or re-trigger web clipping for a bookmark.

    This fetches the page with a headless browser, extracts the article content
    as Markdown, downloads images, and captures a thumbnail screenshot.

    Args:
        bookmark_id: Bookmark ID
    """
    key_info = await authenticate()

    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted:
        raise ToolError(f"Bookmark not found: {bookmark_id}")

    check_project_access(bm.project_id, key_info["project_scopes"])
    await _check_project_not_locked(bm.project_id)

    bm.clip_status = ClipStatus.pending
    bm.clip_error = ""
    await bm.save_updated()

    asyncio.create_task(_run_clip_bg(str(bm.id)))
    return {"status": "pending", "bookmark_id": bookmark_id}


# ── Background helper ───────────────────────────────────────


async def _run_clip_bg(bookmark_id: str) -> None:
    """Background task wrapper for web clipping."""
    try:
        from ...services.bookmark_clip import clip_bookmark as _clip

        bm = await Bookmark.get(bookmark_id)
        if bm and not bm.is_deleted:
            await _clip(bm)
            # Index after clipping
            await index_bookmark(bm)
    except Exception:
        logger.exception("Background clip failed for bookmark %s", bookmark_id)
        try:
            bm = await Bookmark.get(bookmark_id)
            if bm:
                bm.clip_status = ClipStatus.failed
                bm.clip_error = "Unexpected error during clipping"
                await bm.save_updated()
        except Exception:
            pass
