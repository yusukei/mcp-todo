import logging

from fastmcp.exceptions import ToolError

from ...models.knowledge import Knowledge, KnowledgeCategory
from ...services.knowledge_search import index_knowledge, deindex_knowledge
from ...services.serializers import knowledge_to_dict as _knowledge_dict
from ..auth import authenticate
from ..server import mcp

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {e.value for e in KnowledgeCategory}


@mcp.tool()
async def create_knowledge(
    title: str,
    content: str,
    tags: list[str] | None = None,
    category: str = "reference",
    source: str | None = None,
) -> dict:
    """Create a new knowledge entry. Knowledge entries are cross-project and accessible from any MCP client.

    Args:
        title: Short descriptive title (max 255 chars)
        content: Markdown body with the knowledge content (max 50000 chars)
        tags: Categorization tags (e.g., ["tantivy", "japanese", "search"])
        category: One of: recipe, reference, tip, troubleshooting, architecture (default: reference)
        source: Optional source reference (URL, file path, documentation link)
    """
    if not title or not title.strip():
        raise ToolError("Title is required")
    if len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if len(content) > 50000:
        raise ToolError("Content exceeds maximum length of 50000 characters")
    if category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    key_info = await authenticate()
    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    normalized_tags = [t.strip().lower() for t in (tags or []) if t.strip()]

    k = Knowledge(
        title=title.strip(),
        content=content,
        tags=normalized_tags,
        category=KnowledgeCategory(category),
        source=source,
        created_by=creator,
    )
    await k.insert()
    await index_knowledge(k)
    return _knowledge_dict(k)


@mcp.tool()
async def search_knowledge(
    query: str,
    category: str | None = None,
    tag: str | None = None,
    limit: int = 20,
    skip: int = 0,
) -> dict:
    """Search knowledge entries by keyword across title, content, and tags.

    Uses Tantivy full-text search with Japanese morphological analysis (Lindera)
    when available, falling back to MongoDB $regex for substring matching.

    Args:
        query: Search keyword (supports Tantivy query syntax when full-text search is available)
        category: Filter by category (recipe, reference, tip, troubleshooting, architecture)
        tag: Filter by tag
        limit: Maximum number of results (default 20, max 100)
        skip: Number of results to skip for pagination
    """
    if not query or not query.strip():
        raise ToolError("Query is required")
    if category and category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    await authenticate()
    limit = min(max(1, limit), 100)
    skip = max(0, skip)

    # Try Tantivy full-text search first
    from ...services.knowledge_search import KnowledgeSearchService
    search_svc = KnowledgeSearchService.get_instance()
    if search_svc is not None:
        try:
            result = search_svc.search(
                query_text=query.strip(),
                category=category,
                limit=limit + skip,  # fetch enough to apply skip
                offset=0,
            )
            if result.results:
                kid_list = [r["knowledge_id"] for r in result.results]
                entries = await Knowledge.find(
                    {"_id": {"$in": [__import__("bson").ObjectId(kid) for kid in kid_list]}},
                    Knowledge.is_deleted == False,  # noqa: E712
                ).to_list()
                entry_map = {str(e.id): e for e in entries}

                # Apply tag filter post-search
                items = []
                for r in result.results:
                    e = entry_map.get(r["knowledge_id"])
                    if e and (not tag or tag.lower() in e.tags):
                        items.append({**_knowledge_dict(e), "_score": r["score"]})

                # Apply skip/limit
                paginated = items[skip:skip + limit]
                return {
                    "items": paginated,
                    "total": len(items),
                    "limit": limit,
                    "skip": skip,
                    "_meta": {"search_engine": "tantivy"},
                }
        except Exception as e:
            logger.warning("Tantivy knowledge search failed, falling back to regex: %s", e)

    # Fallback: MongoDB $regex
    import re
    pattern = re.escape(query.strip())
    filters: dict = {
        "is_deleted": False,
        "$or": [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"content": {"$regex": pattern, "$options": "i"}},
            {"tags": {"$regex": pattern, "$options": "i"}},
        ],
    }
    if category:
        filters["category"] = category
    if tag:
        filters["tags"] = tag.lower()

    total = await Knowledge.find(filters).count()
    entries = await Knowledge.find(filters).skip(skip).limit(limit).sort("-updated_at").to_list()

    return {
        "items": [_knowledge_dict(e) for e in entries],
        "total": total,
        "limit": limit,
        "skip": skip,
        "_meta": {"search_engine": "regex"},
    }


@mcp.tool()
async def get_knowledge(knowledge_id: str) -> dict:
    """Get a single knowledge entry by ID.

    Args:
        knowledge_id: Knowledge entry ID
    """
    await authenticate()

    k = await Knowledge.get(knowledge_id)
    if not k or k.is_deleted:
        raise ToolError(f"Knowledge entry not found: {knowledge_id}")
    return _knowledge_dict(k)


@mcp.tool()
async def update_knowledge(
    knowledge_id: str,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    source: str | None = None,
) -> dict:
    """Update a knowledge entry. Only provided fields are changed.

    Args:
        knowledge_id: Knowledge entry ID
        title: New title (max 255 chars)
        content: New content (max 50000 chars)
        tags: New tags (replaces existing tags)
        category: New category (recipe, reference, tip, troubleshooting, architecture)
        source: New source reference (pass empty string to clear)
    """
    if title is not None and len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if content is not None and len(content) > 50000:
        raise ToolError("Content exceeds maximum length of 50000 characters")
    if category is not None and category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    await authenticate()

    k = await Knowledge.get(knowledge_id)
    if not k or k.is_deleted:
        raise ToolError(f"Knowledge entry not found: {knowledge_id}")

    if title is not None:
        k.title = title.strip()
    if content is not None:
        k.content = content
    if tags is not None:
        k.tags = [t.strip().lower() for t in tags if t.strip()]
    if category is not None:
        k.category = KnowledgeCategory(category)
    if source is not None:
        k.source = source if source else None

    await k.save_updated()
    await index_knowledge(k)
    return _knowledge_dict(k)


@mcp.tool()
async def delete_knowledge(knowledge_id: str) -> dict:
    """Soft-delete a knowledge entry.

    Args:
        knowledge_id: Knowledge entry ID
    """
    await authenticate()

    k = await Knowledge.get(knowledge_id)
    if not k or k.is_deleted:
        raise ToolError(f"Knowledge entry not found: {knowledge_id}")

    k.is_deleted = True
    await k.save_updated()
    await deindex_knowledge(knowledge_id)
    return {"success": True, "knowledge_id": knowledge_id}


@mcp.tool()
async def list_knowledge(
    category: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List knowledge entries with optional category and tag filters.

    Args:
        category: Filter by category (recipe, reference, tip, troubleshooting, architecture)
        tag: Filter by tag
        limit: Maximum number of results (default 50, max 100)
        skip: Number of results to skip for pagination
    """
    if category and category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    await authenticate()
    limit = min(max(1, limit), 100)
    skip = max(0, skip)

    filters: dict = {"is_deleted": False}
    if category:
        filters["category"] = category
    if tag:
        filters["tags"] = tag.lower()

    total = await Knowledge.find(filters).count()
    entries = await Knowledge.find(filters).skip(skip).limit(limit).sort("-updated_at").to_list()

    return {
        "items": [_knowledge_dict(e) for e in entries],
        "total": total,
        "limit": limit,
        "skip": skip,
    }
