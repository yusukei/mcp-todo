import asyncio
import re

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Bookmark, BookmarkCollection, Project, User
from ....models.bookmark import BookmarkMetadata, ClipStatus
from ....services.bookmark_cleanup import cleanup_bookmark_assets
from ....services.serializers import (
    bookmark_collection_to_dict as _coll_dict,
    bookmark_summary as _bookmark_summary,
    bookmark_to_dict as _bookmark_dict,
)

router = APIRouter(tags=["bookmarks"])


# ── Request models ──────────────────────────────────────────


class CreateBookmarkRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    title: str = Field("", max_length=255)
    description: str = Field("", max_length=10000)
    tags: list[str] = Field(default_factory=list)
    collection_id: str | None = None


class UpdateBookmarkRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    tags: list[str] | None = None
    collection_id: str | None = None
    is_starred: bool | None = None


class CreateCollectionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=10000)
    icon: str = Field("folder", max_length=50)
    color: str = Field("#6366f1", max_length=20)


class UpdateCollectionRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    icon: str | None = Field(None, max_length=50)
    color: str | None = Field(None, max_length=20)


class ReorderRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=200)


# ── Helpers ─────────────────────────────────────────────────


async def _check_project_access(project_id: str, user: User) -> Project:
    from ....models.project import ProjectStatus as _ProjectStatus

    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project or project.status == _ProjectStatus.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return project


def _check_not_locked(project: Project) -> None:
    if project.is_locked:
        raise HTTPException(status.HTTP_423_LOCKED, "Project is locked")


# ── Collection Endpoints ────────────────────────────────────

coll_router = APIRouter(
    prefix="/projects/{project_id}/bookmark-collections",
    tags=["bookmark-collections"],
)


@coll_router.post("/", status_code=status.HTTP_201_CREATED)
async def create_collection(
    project_id: str,
    body: CreateCollectionRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    c = BookmarkCollection(
        project_id=project_id,
        name=body.name.strip(),
        description=body.description,
        icon=body.icon,
        color=body.color,
        created_by=str(user.id),
    )
    await c.insert()
    return _coll_dict(c)


@coll_router.get("/")
async def list_collections(
    project_id: str,
    user: User = Depends(get_current_user),
):
    await _check_project_access(project_id, user)
    items = (
        await BookmarkCollection.find(
            {"project_id": project_id, "is_deleted": False},
        )
        .sort("+sort_order", "+name")
        .to_list()
    )
    return {"items": [_coll_dict(c) for c in items]}


@coll_router.patch("/{collection_id}")
async def update_collection(
    project_id: str,
    collection_id: str,
    body: UpdateCollectionRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    valid_object_id(collection_id)
    c = await BookmarkCollection.get(collection_id)
    if not c or c.is_deleted or c.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    if body.name is not None:
        c.name = body.name.strip()
    if body.description is not None:
        c.description = body.description
    if body.icon is not None:
        c.icon = body.icon
    if body.color is not None:
        c.color = body.color

    await c.save_updated()
    return _coll_dict(c)


@coll_router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    project_id: str,
    collection_id: str,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    valid_object_id(collection_id)
    c = await BookmarkCollection.get(collection_id)
    if not c or c.is_deleted or c.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    c.is_deleted = True
    await c.save_updated()

    # Unset collection_id on bookmarks in this collection
    await Bookmark.find(
        {"collection_id": collection_id, "is_deleted": False},
    ).update({"$set": {"collection_id": None}})


@coll_router.post("/reorder")
async def reorder_collections(
    project_id: str,
    body: ReorderRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    try:
        oids = [ObjectId(cid) for cid in body.ids]
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid collection ID")

    items = await BookmarkCollection.find(
        {"_id": {"$in": oids}, "project_id": project_id, "is_deleted": False},
    ).to_list()
    item_map = {str(c.id): c for c in items}

    updates = []
    for i, cid in enumerate(body.ids):
        c = item_map.get(cid)
        if c and c.sort_order != i:
            c.sort_order = i
            updates.append(c.save())
    if updates:
        await asyncio.gather(*updates)

    return {"reordered": len(updates)}


# ── Bookmark Endpoints ──────────────────────────────────────

bm_router = APIRouter(
    prefix="/projects/{project_id}/bookmarks",
    tags=["bookmarks"],
)


@bm_router.post("/", status_code=status.HTTP_201_CREATED)
async def create_bookmark(
    project_id: str,
    body: CreateBookmarkRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    normalized_tags = [t.strip().lower() for t in body.tags if t.strip()]
    title = body.title.strip() if body.title.strip() else body.url

    bm = Bookmark(
        project_id=project_id,
        url=body.url.strip(),
        title=title,
        description=body.description,
        tags=normalized_tags,
        collection_id=body.collection_id,
        clip_status=ClipStatus.pending,
        created_by=str(user.id),
    )
    await bm.insert()

    # Launch background clipping task
    asyncio.create_task(_run_clip(str(bm.id)))

    return _bookmark_dict(bm)


@bm_router.get("/")
async def list_bookmarks(
    project_id: str,
    collection_id: str | None = Query(None),
    tag: str | None = Query(None),
    starred: bool | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    await _check_project_access(project_id, user)

    filters: dict = {"project_id": project_id, "is_deleted": False}
    if collection_id is not None:
        filters["collection_id"] = collection_id if collection_id != "" else None
    if tag:
        filters["tags"] = tag.lower()
    if starred is not None:
        filters["is_starred"] = starred
    if search:
        pattern = re.escape(search.strip())
        filters["$or"] = [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"url": {"$regex": pattern, "$options": "i"}},
            {"description": {"$regex": pattern, "$options": "i"}},
            {"tags": {"$regex": pattern, "$options": "i"}},
        ]

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


@bm_router.get("/{bookmark_id}")
async def get_bookmark(
    project_id: str,
    bookmark_id: str,
    user: User = Depends(get_current_user),
):
    await _check_project_access(project_id, user)
    valid_object_id(bookmark_id)
    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted or bm.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")
    return _bookmark_dict(bm)


@bm_router.patch("/{bookmark_id}")
async def update_bookmark(
    project_id: str,
    bookmark_id: str,
    body: UpdateBookmarkRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    valid_object_id(bookmark_id)
    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted or bm.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")

    if body.title is not None:
        bm.title = body.title.strip()
    if body.description is not None:
        bm.description = body.description
    if body.tags is not None:
        bm.tags = [t.strip().lower() for t in body.tags if t.strip()]
    if body.collection_id is not None:
        bm.collection_id = body.collection_id if body.collection_id != "" else None
    if body.is_starred is not None:
        bm.is_starred = body.is_starred

    await bm.save_updated()
    return _bookmark_dict(bm)


@bm_router.delete("/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bookmark(
    project_id: str,
    bookmark_id: str,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    valid_object_id(bookmark_id)
    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted or bm.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")

    bm.is_deleted = True
    await bm.save_updated()
    await cleanup_bookmark_assets(str(bm.id))


@bm_router.post("/{bookmark_id}/clip")
async def reclip_bookmark(
    project_id: str,
    bookmark_id: str,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    valid_object_id(bookmark_id)
    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted or bm.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")

    bm.clip_status = ClipStatus.pending
    bm.clip_error = ""
    await bm.save_updated()

    asyncio.create_task(_run_clip(str(bm.id)))
    return {"status": "pending", "bookmark_id": str(bm.id)}


@bm_router.post("/reorder")
async def reorder_bookmarks(
    project_id: str,
    body: ReorderRequest,
    user: User = Depends(get_current_user),
):
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    try:
        oids = [ObjectId(bid) for bid in body.ids]
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid bookmark ID")

    items = await Bookmark.find(
        {"_id": {"$in": oids}, "project_id": project_id, "is_deleted": False},
    ).to_list()
    item_map = {str(b.id): b for b in items}

    updates = []
    for i, bid in enumerate(body.ids):
        b = item_map.get(bid)
        if b and b.sort_order != i:
            b.sort_order = i
            updates.append(b.save())
    if updates:
        await asyncio.gather(*updates)

    return {"reordered": len(updates)}


# ── Import ──────────────────────────────────────────────────

_MAX_IMPORT_SIZE = 10 * 1024 * 1024  # 10MB


@bm_router.post("/import")
async def import_bookmarks(
    project_id: str,
    file: UploadFile,
    collection_id: str | None = Query(None),
    user: User = Depends(get_current_user),
):
    """Import bookmarks from a Raindrop.io CSV export."""
    project = await _check_project_access(project_id, user)
    _check_not_locked(project)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only CSV files are supported")

    content = await file.read()
    if len(content) > _MAX_IMPORT_SIZE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File too large (max {_MAX_IMPORT_SIZE // 1024 // 1024}MB)")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "File encoding must be UTF-8")

    from ....services.bookmark_import import import_bookmarks as _import

    result = await _import(
        file_content=text,
        project_id=project_id,
        created_by=str(user.id),
        collection_id=collection_id,
    )

    # Start background clipping for imported bookmarks
    if result["total_pending"] > 0:
        asyncio.create_task(_run_clip_pending(project_id))

    return result


async def _run_clip_pending(project_id: str) -> None:
    """Background task: clip all pending bookmarks sequentially."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        from ....services.bookmark_clip import clip_pending_bookmarks
        await clip_pending_bookmarks(project_id)
    except Exception:
        logger.exception("Background clip_pending failed for project %s", project_id)


# ── Background clipping ────────────────────────────────────


async def _run_clip(bookmark_id: str) -> None:
    """Background task wrapper for web clipping."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        from ....services.bookmark_clip import clip_bookmark

        bm = await Bookmark.get(bookmark_id)
        if bm and not bm.is_deleted:
            await clip_bookmark(bm)
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
