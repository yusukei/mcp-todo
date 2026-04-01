import logging

from fastmcp.exceptions import ToolError

from ...models.docsite import DocPage, DocSite
from ...services.serializers import docpage_to_dict, docsite_summary, docsite_to_dict
from ..auth import authenticate
from ..server import mcp

logger = logging.getLogger(__name__)


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
    site = await DocSite.get(site_id)
    if not site:
        raise ToolError(f"DocSite not found: {site_id}")
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
