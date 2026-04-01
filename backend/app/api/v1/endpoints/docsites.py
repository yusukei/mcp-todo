"""DocSite API — browse imported documentation sites."""

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from ....core.config import settings
from ....core.deps import get_current_user
from ....models.docsite import DocPage, DocSite
from ....models.user import User
from ....services.serializers import docpage_to_dict, docsite_summary, docsite_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/docsites", tags=["docsites"])


# ── Helpers ──────────────────────────────────────────────────

async def _get_site(site_id: str) -> DocSite:
    site = await DocSite.get(site_id)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DocSite not found")
    return site


# ── List / Get ───────────────────────────────────────────────

@router.get("")
async def list_docsites(user: User = Depends(get_current_user)) -> list[dict]:
    """List all imported documentation sites."""
    sites = await DocSite.find_all().sort("-updated_at").to_list()
    return [docsite_summary(s) for s in sites]


@router.get("/{site_id}")
async def get_docsite(site_id: str, user: User = Depends(get_current_user)) -> dict:
    """Get a documentation site with its navigation tree."""
    site = await _get_site(site_id)
    return docsite_to_dict(site)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_docsite(site_id: str, user: User = Depends(get_current_user)) -> None:
    """Delete a documentation site and all its pages."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    site = await _get_site(site_id)

    # Delete all pages
    await DocPage.find(DocPage.site_id == str(site.id)).delete()
    await site.delete()

    # Deindex from search
    from ....services.docsite_search import DocSiteSearchIndexer
    indexer = DocSiteSearchIndexer.get_instance()
    if indexer:
        await indexer.delete_site(str(site.id))


# ── Pages ────────────────────────────────────────────────────

@router.get("/{site_id}/pages")
async def list_pages(
    site_id: str,
    user: User = Depends(get_current_user),
) -> list[dict]:
    """List all pages in a documentation site (without content)."""
    await _get_site(site_id)
    pages = await DocPage.find(DocPage.site_id == site_id).sort("sort_order").to_list()
    return [
        {"id": str(p.id), "site_id": p.site_id, "path": p.path, "title": p.title, "sort_order": p.sort_order}
        for p in pages
    ]


@router.get("/{site_id}/pages/{page_path:path}")
async def get_page(
    site_id: str,
    page_path: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Get a single page by its path."""
    await _get_site(site_id)
    page = await DocPage.find_one(DocPage.site_id == site_id, DocPage.path == page_path)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    return docpage_to_dict(page)


# ── Assets ───────────────────────────────────────────────────

@router.get("/{site_id}/assets/{asset_path:path}")
async def get_asset(
    site_id: str,
    asset_path: str,
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Serve a static asset (image, etc.) from a documentation site."""
    await _get_site(site_id)

    base_dir = Path(settings.DOCSITE_ASSETS_DIR) / site_id
    file_path = (base_dir / asset_path).resolve()

    # Prevent path traversal
    if not str(file_path).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── Search ───────────────────────────────────────────────────

@router.get("/{site_id}/search")
async def search_pages(
    site_id: str,
    q: str,
    limit: int = 20,
    skip: int = 0,
    user: User = Depends(get_current_user),
) -> dict:
    """Search pages within a documentation site."""
    await _get_site(site_id)

    if not q or not q.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query required")

    limit = min(max(1, limit), 100)
    skip = max(0, skip)

    # Try Tantivy first
    from ....services.docsite_search import DocSiteSearchService
    search_svc = DocSiteSearchService.get_instance()
    if search_svc is not None:
        try:
            result = search_svc.search(query_text=q.strip(), site_id=site_id, limit=limit + skip)
            if result.results:
                import bson
                page_ids = [r["page_id"] for r in result.results]
                pages = await DocPage.find(
                    {"_id": {"$in": [bson.ObjectId(pid) for pid in page_ids]}}
                ).to_list()
                page_map = {str(p.id): p for p in pages}

                items = []
                for r in result.results:
                    p = page_map.get(r["page_id"])
                    if p:
                        items.append({**docpage_to_dict(p), "_score": r["score"]})

                paginated = items[skip:skip + limit]
                return {"items": paginated, "total": len(items), "limit": limit, "skip": skip, "_meta": {"search_engine": "tantivy"}}
        except Exception as e:
            logger.warning("Tantivy docsite search failed, falling back to regex: %s", e)

    # Fallback: MongoDB regex
    import re
    pattern = re.escape(q.strip())
    mongo_filters: dict = {
        "site_id": site_id,
        "$or": [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"content": {"$regex": pattern, "$options": "i"}},
        ],
    }
    total = await DocPage.find(mongo_filters).count()
    pages = await DocPage.find(mongo_filters).skip(skip).limit(limit).sort("sort_order").to_list()
    return {
        "items": [docpage_to_dict(p) for p in pages],
        "total": total,
        "limit": limit,
        "skip": skip,
        "_meta": {"search_engine": "regex"},
    }
