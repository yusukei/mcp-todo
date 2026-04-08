import logging

from fastmcp.exceptions import ToolError

from ...models.document import DocumentCategory, DocumentVersion, ProjectDocument
from ...services.document_search import index_document, deindex_document
from ...services.serializers import (
    document_to_dict as _document_dict,
    document_version_summary as _version_summary,
    document_version_to_dict as _version_dict,
)
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _check_project_not_locked, _resolve_project_id

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {e.value for e in DocumentCategory}


@mcp.tool()
async def create_document(
    project_id: str,
    title: str,
    content: str = "",
    tags: list[str] | None = None,
    category: str = "spec",
) -> dict:
    """Create a new document in a project.

    Args:
        project_id: Project ID or project name
        title: Document title (max 255 chars)
        content: Markdown body (max 100000 chars)
        tags: Categorization tags
        category: One of: spec, design, api, guide, notes (default: spec)
    """
    if not title or not title.strip():
        raise ToolError("Title is required")
    if len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if len(content) > 100000:
        raise ToolError("Content exceeds maximum length of 100000 characters")
    if category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)
    await _check_project_not_locked(project_id)

    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"
    normalized_tags = [t.strip().lower() for t in (tags or []) if t.strip()]

    d = ProjectDocument(
        project_id=project_id,
        title=title.strip(),
        content=content,
        tags=normalized_tags,
        category=DocumentCategory(category),
        created_by=creator,
    )
    await d.insert()
    await index_document(d)
    return _document_dict(d)


@mcp.tool()
async def get_document(document_id: str) -> dict:
    """Get a single project document by ID.

    Args:
        document_id: Document ID
    """
    key_info = await authenticate()

    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted:
        raise ToolError(f"Document not found: {document_id}")

    await check_project_access(d.project_id, key_info)
    return _document_dict(d)


@mcp.tool()
async def update_document(
    document_id: str,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    task_id: str | None = None,
    change_summary: str | None = None,
) -> dict:
    """Update a project document. Only provided fields are changed.

    Each update automatically creates a version snapshot of the previous state.
    Use get_document_history to view past versions.

    Args:
        document_id: Document ID
        title: New title (max 255 chars)
        content: New content (max 100000 chars)
        tags: New tags (replaces existing tags)
        category: New category (spec, design, api, guide, notes)
        task_id: Optional task ID that triggered this change (for traceability)
        change_summary: Optional short description of what changed
    """
    if title is not None and len(title) > 255:
        raise ToolError("Title exceeds maximum length of 255 characters")
    if content is not None and len(content) > 100000:
        raise ToolError("Content exceeds maximum length of 100000 characters")
    if category is not None and category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    key_info = await authenticate()

    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted:
        raise ToolError(f"Document not found: {document_id}")

    await check_project_access(d.project_id, key_info)
    await _check_project_not_locked(d.project_id)

    changer = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else "mcp"

    # Snapshot current state as a version before applying changes
    version = DocumentVersion(
        document_id=str(d.id),
        version=d.version,
        title=d.title,
        content=d.content,
        tags=list(d.tags),
        category=d.category,
        changed_by=changer,
        task_id=task_id,
        change_summary=change_summary,
    )
    await version.insert()

    # Apply updates
    if title is not None:
        d.title = title.strip()
    if content is not None:
        d.content = content
    if tags is not None:
        d.tags = [t.strip().lower() for t in tags if t.strip()]
    if category is not None:
        d.category = DocumentCategory(category)
    d.version += 1

    await d.save_updated()
    await index_document(d)
    return _document_dict(d)


@mcp.tool()
async def delete_document(document_id: str) -> dict:
    """Soft-delete a project document.

    Args:
        document_id: Document ID
    """
    key_info = await authenticate()

    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted:
        raise ToolError(f"Document not found: {document_id}")

    await check_project_access(d.project_id, key_info)
    await _check_project_not_locked(d.project_id)

    d.is_deleted = True
    await d.save_updated()
    await deindex_document(str(d.id))
    return {"success": True, "document_id": str(d.id)}


@mcp.tool()
async def list_documents(
    project_id: str,
    category: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List documents in a project.

    Args:
        project_id: Project ID or project name
        category: Filter by category (spec, design, api, guide, notes)
        tag: Filter by tag
        limit: Maximum number of results (default 50, max 100)
        skip: Number of results to skip for pagination
    """
    if category and category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    limit = min(max(1, limit), 100)
    skip = max(0, skip)

    filters: dict = {"project_id": project_id, "is_deleted": False}
    if category:
        filters["category"] = category
    if tag:
        filters["tags"] = tag.lower()

    total = await ProjectDocument.find(filters).count()
    docs = await ProjectDocument.find(filters).skip(skip).limit(limit).sort("-updated_at").to_list()

    return {
        "items": [_document_dict(d) for d in docs],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@mcp.tool()
async def search_documents(
    query: str,
    project_id: str,
    category: str | None = None,
    tag: str | None = None,
    limit: int = 20,
    skip: int = 0,
) -> dict:
    """Search project documents by keyword across title, content, and tags.

    Uses Tantivy full-text search with Japanese morphological analysis (Lindera)
    when available, falling back to MongoDB $regex for substring matching.

    Args:
        query: Search keyword (supports Tantivy query syntax when full-text search is available)
        project_id: Project ID or project name (required — cross-project search
            is not supported; loop over projects from list_projects if needed)
        category: Filter by category (spec, design, api, guide, notes)
        tag: Filter by tag
        limit: Maximum number of results (default 20, max 100)
        skip: Number of results to skip for pagination
    """
    if not query or not query.strip():
        raise ToolError("Query is required")
    if category and category not in _VALID_CATEGORIES:
        raise ToolError(f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}")

    key_info = await authenticate()

    resolved_project_id = await _resolve_project_id(project_id)
    await check_project_access(resolved_project_id, key_info)

    limit = min(max(1, limit), 100)
    skip = max(0, skip)

    # Try Tantivy full-text search first
    from ...services.document_search import DocumentSearchService
    search_svc = DocumentSearchService.get_instance()
    if search_svc is not None:
        try:
            result = search_svc.search(
                query_text=query.strip(),
                project_id=resolved_project_id,
                category=category,
                limit=limit + skip,
                offset=0,
            )
            if result.results:
                import bson
                did_list = [r["document_id"] for r in result.results]
                filters: dict = {
                    "_id": {"$in": [bson.ObjectId(did) for did in did_list]},
                    "is_deleted": False,
                    "project_id": resolved_project_id,
                }

                entries = await ProjectDocument.find(filters).to_list()
                entry_map = {str(e.id): e for e in entries}

                items = []
                for r in result.results:
                    e = entry_map.get(r["document_id"])
                    if e and (not tag or tag.lower() in e.tags):
                        items.append({**_document_dict(e), "_score": r["score"]})

                paginated = items[skip:skip + limit]
                return {
                    "items": paginated,
                    "total": len(items),
                    "limit": limit,
                    "skip": skip,
                    "_meta": {"search_engine": "tantivy"},
                }
        except Exception as e:
            logger.warning("Tantivy document search failed, falling back to regex: %s", e)

    # Fallback: MongoDB $regex
    import re
    pattern = re.escape(query.strip())
    mongo_filters: dict = {
        "is_deleted": False,
        "project_id": resolved_project_id,
        "$or": [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"content": {"$regex": pattern, "$options": "i"}},
            {"tags": {"$regex": pattern, "$options": "i"}},
        ],
    }
    if category:
        mongo_filters["category"] = category
    if tag:
        mongo_filters["tags"] = tag.lower()

    total = await ProjectDocument.find(mongo_filters).count()
    entries = await ProjectDocument.find(mongo_filters).skip(skip).limit(limit).sort("-updated_at").to_list()

    return {
        "items": [_document_dict(e) for e in entries],
        "total": total,
        "limit": limit,
        "skip": skip,
        "_meta": {"search_engine": "regex"},
    }


@mcp.tool()
async def get_document_history(
    document_id: str,
    limit: int = 20,
    skip: int = 0,
) -> dict:
    """Get version history of a document (summaries without content).

    Args:
        document_id: Document ID
        limit: Maximum number of versions (default 20, max 100)
        skip: Number of versions to skip for pagination
    """
    key_info = await authenticate()

    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted:
        raise ToolError(f"Document not found: {document_id}")

    await check_project_access(d.project_id, key_info)

    limit = min(max(1, limit), 100)
    skip = max(0, skip)

    total = await DocumentVersion.find(
        DocumentVersion.document_id == str(d.id),
    ).count()
    versions = await DocumentVersion.find(
        DocumentVersion.document_id == str(d.id),
    ).sort("-version").skip(skip).limit(limit).to_list()

    return {
        "document_id": str(d.id),
        "current_version": d.version,
        "items": [_version_summary(v) for v in versions],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@mcp.tool()
async def get_document_version(
    document_id: str,
    version: int,
) -> dict:
    """Get a specific version of a document (with full content).

    Args:
        document_id: Document ID
        version: Version number to retrieve
    """
    key_info = await authenticate()

    d = await ProjectDocument.get(document_id)
    if not d or d.is_deleted:
        raise ToolError(f"Document not found: {document_id}")

    await check_project_access(d.project_id, key_info)

    v = await DocumentVersion.find_one(
        DocumentVersion.document_id == str(d.id),
        DocumentVersion.version == version,
    )
    if not v:
        raise ToolError(f"Version {version} not found for document {document_id}")

    return _version_dict(v)
