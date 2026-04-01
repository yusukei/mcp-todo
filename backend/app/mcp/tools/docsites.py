import logging

from fastmcp.exceptions import ToolError

from ...models.docsite import DocPage, DocSite
from ...services.serializers import docpage_to_dict, docsite_summary, docsite_to_dict
from ..auth import authenticate
from ..server import mcp

logger = logging.getLogger(__name__)


async def _get_site_or_error(site_id: str) -> DocSite:
    site = await DocSite.get(site_id)
    if not site:
        raise ToolError(f"DocSite not found: {site_id}")
    return site


@mcp.tool()
async def list_docsites() -> list[dict]:
    """List all imported documentation sites.

    Returns a summary of each site (without navigation tree).
    """
    await authenticate()
    sites = await DocSite.find_all().sort("-updated_at").to_list()
    return [docsite_summary(s) for s in sites]


@mcp.tool()
async def get_docsite(site_id: str) -> dict:
    """Get a documentation site with its full navigation tree.

    Args:
        site_id: DocSite ID
    """
    await authenticate()
    site = await _get_site_or_error(site_id)
    return docsite_to_dict(site)


@mcp.tool()
async def get_docpage(
    site_id: str,
    path: str,
) -> dict:
    """Get a documentation page by its path within a site.

    Args:
        site_id: DocSite ID
        path: Page path (e.g. "document/discover/6-dof-and-3-dof")
    """
    await authenticate()
    page = await DocPage.find_one(DocPage.site_id == site_id, DocPage.path == path)
    if not page:
        raise ToolError(f"Page not found: {path} in site {site_id}")
    return docpage_to_dict(page)


@mcp.tool()
async def search_docpages(
    query: str,
    site_id: str | None = None,
    limit: int = 20,
    skip: int = 0,
) -> dict:
    """Search documentation pages by keyword across title and content.

    Uses Tantivy full-text search with Japanese morphological analysis (Lindera)
    when available, falling back to MongoDB $regex for substring matching.

    Args:
        query: Search keyword
        site_id: Filter by DocSite ID (optional; omit to search all sites)
        limit: Maximum number of results (default 20, max 100)
        skip: Number of results to skip for pagination
    """
    if not query or not query.strip():
        raise ToolError("Query is required")

    await authenticate()

    limit = min(max(1, limit), 100)
    skip = max(0, skip)

    # Try Tantivy first
    from ...services.docsite_search import DocSiteSearchService
    search_svc = DocSiteSearchService.get_instance()
    if search_svc is not None:
        try:
            result = search_svc.search(
                query_text=query.strip(),
                site_id=site_id,
                limit=limit + skip,
            )
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
                return {
                    "items": paginated,
                    "total": len(items),
                    "limit": limit,
                    "skip": skip,
                    "_meta": {"search_engine": "tantivy"},
                }
        except Exception as e:
            logger.warning("Tantivy docsite search failed, falling back to regex: %s", e)

    # Fallback: MongoDB regex
    import re
    pattern = re.escape(query.strip())
    mongo_filters: dict = {
        "$or": [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"content": {"$regex": pattern, "$options": "i"}},
        ],
    }
    if site_id:
        mongo_filters["site_id"] = site_id

    total = await DocPage.find(mongo_filters).count()
    pages = await DocPage.find(mongo_filters).skip(skip).limit(limit).sort("sort_order").to_list()

    return {
        "items": [docpage_to_dict(p) for p in pages],
        "total": total,
        "limit": limit,
        "skip": skip,
        "_meta": {"search_engine": "regex"},
    }


@mcp.tool()
async def update_docpage(
    site_id: str,
    path: str,
    title: str | None = None,
    content: str | None = None,
) -> dict:
    """Update a documentation page's title or content.

    Use this to fix translations, correct formatting issues, or update
    page content. Only provided fields are changed.

    Args:
        site_id: DocSite ID
        path: Page path (e.g. "document/unity/pico-building-blocks")
        title: New title (optional)
        content: New content in Markdown (optional, max 200000 chars)
    """
    if title is not None and len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if content is not None and len(content) > 200000:
        raise ToolError("Content exceeds maximum length of 200000 characters")

    await authenticate()
    await _get_site_or_error(site_id)

    page = await DocPage.find_one(DocPage.site_id == site_id, DocPage.path == path)
    if not page:
        raise ToolError(f"Page not found: {path} in site {site_id}")

    if title is not None:
        page.title = title.strip()
    if content is not None:
        page.content = content

    await page.save()

    # Update search index
    from ...services.docsite_search import DocSiteSearchIndexer
    indexer = DocSiteSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.upsert_page(page)
        except Exception as e:
            logger.warning("Failed to update search index for page %s: %s", path, e)

    return docpage_to_dict(page)


@mcp.tool()
async def create_docpage(
    site_id: str,
    path: str,
    title: str,
    content: str = "",
) -> dict:
    """Create a new documentation page in a site.

    Use this to add missing pages (e.g. pages that were not crawled).

    Args:
        site_id: DocSite ID
        path: Page path (e.g. "document/unity/spatial-audio")
        title: Page title (max 255 chars)
        content: Page content in Markdown (max 200000 chars)
    """
    if not title or not title.strip():
        raise ToolError("Title is required")
    if len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if len(content) > 200000:
        raise ToolError("Content exceeds maximum length of 200000 characters")

    await authenticate()
    site = await _get_site_or_error(site_id)

    existing = await DocPage.find_one(DocPage.site_id == site_id, DocPage.path == path)
    if existing:
        raise ToolError(f"Page already exists at path: {path}. Use update_docpage instead.")

    # Determine sort_order (append at end)
    last = await DocPage.find(DocPage.site_id == site_id).sort("-sort_order").limit(1).to_list()
    sort_order = (last[0].sort_order + 1) if last else 0

    page = DocPage(
        site_id=site_id,
        path=path,
        title=title.strip(),
        content=content,
        sort_order=sort_order,
    )
    await page.insert()

    # Update page count
    site.page_count = await DocPage.find(DocPage.site_id == site_id).count()
    await site.save_updated()

    # Update search index
    from ...services.docsite_search import DocSiteSearchIndexer
    indexer = DocSiteSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.upsert_page(page)
        except Exception as e:
            logger.warning("Failed to index new page %s: %s", path, e)

    return docpage_to_dict(page)


@mcp.tool()
async def delete_docpage(
    site_id: str,
    path: str,
) -> dict:
    """Delete a documentation page from a site.

    Args:
        site_id: DocSite ID
        path: Page path to delete
    """
    await authenticate()
    await _get_site_or_error(site_id)

    page = await DocPage.find_one(DocPage.site_id == site_id, DocPage.path == path)
    if not page:
        raise ToolError(f"Page not found: {path} in site {site_id}")

    page_id = str(page.id)
    await page.delete()

    # Update page count
    site = await _get_site_or_error(site_id)
    site.page_count = await DocPage.find(DocPage.site_id == site_id).count()
    await site.save_updated()

    # Remove from search index
    from ...services.docsite_search import DocSiteSearchIndexer
    indexer = DocSiteSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.delete_page(page_id)
        except Exception as e:
            logger.warning("Failed to deindex page %s: %s", path, e)

    return {"success": True, "path": path}


@mcp.tool()
async def upload_docsite_asset(
    site_id: str,
    asset_path: str,
    data_base64: str,
) -> dict:
    """Upload a static asset (image, etc.) for a documentation site.

    Use this to add images referenced by documentation pages.
    The asset will be served at /api/v1/docsites/{site_id}/assets/{asset_path}.

    Args:
        site_id: DocSite ID
        asset_path: Asset path relative to the site root (e.g. "document/unity/spatial-audio/images/img_001.webp")
        data_base64: Base64-encoded file content
    """
    import base64
    from pathlib import Path

    from ...core.config import settings

    await authenticate()
    await _get_site_or_error(site_id)

    # Validate extension
    ext = Path(asset_path).suffix.lower()
    allowed = {
        ".webp", ".avif", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
        ".pdf", ".mp4", ".webm", ".woff2", ".woff",
    }
    if ext not in allowed:
        raise ToolError(f"Unsupported file type: {ext}")

    # Decode
    try:
        content = base64.b64decode(data_base64)
    except Exception:
        raise ToolError("Invalid base64 data")

    if len(content) > 20 * 1024 * 1024:  # 20MB limit
        raise ToolError("File too large (max 20MB)")

    # Write file
    base_dir = Path(settings.DOCSITE_ASSETS_DIR) / site_id
    file_path = (base_dir / asset_path).resolve()

    # Prevent path traversal
    if not str(file_path).startswith(str(base_dir.resolve())):
        raise ToolError("Invalid asset path")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)

    return {"path": asset_path, "size": len(content)}
