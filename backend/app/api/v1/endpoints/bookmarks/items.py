"""Bookmark item CRUD + batch + clip + reorder + CSV import endpoints."""
from __future__ import annotations

import asyncio
import re

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status

from .....core.deps import get_current_user
from .....core.validators import valid_object_id
from .....models import Bookmark, User
from .....models.bookmark import ClipStatus
from .....services.bookmark_cleanup import cleanup_bookmark_assets
from .....services.clip_queue import clip_queue
from .....services.serializers import (
    bookmark_summary as _bookmark_summary,
    bookmark_to_dict as _bookmark_dict,
)
from ._shared import (
    BatchBookmarkAction,
    CreateBookmarkRequest,
    ReorderRequest,
    UpdateBookmarkRequest,
    check_not_locked,
    check_project_access,
)

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
    project = await check_project_access(project_id, user)
    check_not_locked(project)

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

    # Enqueue for background clipping
    await clip_queue.enqueue(str(bm.id))

    return _bookmark_dict(bm)


@bm_router.get("/")
async def list_bookmarks(
    project_id: str,
    collection_id: str | None = Query(None),
    tag: str | None = Query(None),
    starred: bool | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    await check_project_access(project_id, user)

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
        .sort("+sort_order", "-created_at")
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
    await check_project_access(project_id, user)
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
    project = await check_project_access(project_id, user)
    check_not_locked(project)

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
    project = await check_project_access(project_id, user)
    check_not_locked(project)

    valid_object_id(bookmark_id)
    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted or bm.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")

    bm.is_deleted = True
    await bm.save_updated()
    await cleanup_bookmark_assets(str(bm.id))


_VALID_BATCH_ACTIONS = {"delete", "star", "unstar", "set_collection", "add_tags", "remove_tags"}


@bm_router.post("/batch")
async def batch_bookmark_action(
    project_id: str,
    body: BatchBookmarkAction,
    user: User = Depends(get_current_user),
):
    """Apply a bulk action to multiple bookmarks."""
    project = await check_project_access(project_id, user)
    check_not_locked(project)

    if body.action not in _VALID_BATCH_ACTIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid action: {body.action}")
    if body.action == "set_collection" and body.collection_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "collection_id required for set_collection")
    if body.action in ("add_tags", "remove_tags") and not body.tags:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tags required for add_tags/remove_tags")

    for bid in body.bookmark_ids:
        valid_object_id(bid)

    bookmarks = await Bookmark.find(
        {"_id": {"$in": [ObjectId(bid) for bid in body.bookmark_ids]}},
        Bookmark.project_id == project_id,
        Bookmark.is_deleted == False,
    ).to_list()
    bm_map = {str(b.id): b for b in bookmarks}

    updated = []
    failed = []

    for bid in body.bookmark_ids:
        bm = bm_map.get(bid)
        if not bm:
            failed.append({"id": bid, "error": "Not found"})
            continue

        if body.action == "delete":
            bm.is_deleted = True
        elif body.action == "star":
            bm.is_starred = True
        elif body.action == "unstar":
            bm.is_starred = False
        elif body.action == "set_collection":
            bm.collection_id = body.collection_id if body.collection_id != "" else None
        elif body.action == "add_tags":
            existing = set(bm.tags)
            for t in body.tags:
                tag = t.strip().lower()
                if tag:
                    existing.add(tag)
            bm.tags = sorted(existing)
        elif body.action == "remove_tags":
            remove = {t.strip().lower() for t in body.tags if t.strip()}
            bm.tags = [t for t in bm.tags if t not in remove]

        updated.append(bm)

    results = await asyncio.gather(
        *[b.save_updated() for b in updated], return_exceptions=True,
    )

    saved = []
    for bm, result in zip(updated, results):
        if isinstance(result, Exception):
            failed.append({"id": str(bm.id), "error": str(result)})
        else:
            saved.append(str(bm.id))

    # Cleanup assets for deleted bookmarks
    if body.action == "delete":
        await asyncio.gather(*[cleanup_bookmark_assets(bid) for bid in saved])

    return {"affected": len(saved), "failed": failed}


@bm_router.post("/{bookmark_id}/clip")
async def reclip_bookmark(
    project_id: str,
    bookmark_id: str,
    user: User = Depends(get_current_user),
):
    project = await check_project_access(project_id, user)
    check_not_locked(project)

    valid_object_id(bookmark_id)
    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted or bm.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")

    bm.clip_status = ClipStatus.pending
    bm.clip_error = ""
    await bm.save_updated()

    await clip_queue.enqueue(str(bm.id))
    return {"status": "pending", "bookmark_id": str(bm.id)}


@bm_router.post("/reorder")
async def reorder_bookmarks(
    project_id: str,
    body: ReorderRequest,
    user: User = Depends(get_current_user),
):
    project = await check_project_access(project_id, user)
    check_not_locked(project)

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
    project = await check_project_access(project_id, user)
    check_not_locked(project)

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

    from .....services.bookmark_import import import_bookmarks as _import

    result = await _import(
        file_content=text,
        project_id=project_id,
        created_by=str(user.id),
        collection_id=collection_id,
    )

    # Enqueue imported bookmarks for background clipping
    imported_ids = result.get("imported_ids", [])
    if imported_ids:
        await clip_queue.enqueue_many(imported_ids)

    return result
