"""Shared serialization helpers for Task and Project models.

Used by both the REST API endpoints and MCP tools to ensure
consistent response format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Project, Task
    from ..models.bookmark import Bookmark, BookmarkCollection
    from ..models.docsite import DocPage, DocSite, DocSiteSection
    from ..models.document import DocumentVersion, ProjectDocument
    from ..models.knowledge import Knowledge
    from ..models.secret import ProjectSecret


def task_to_dict(t: Task) -> dict:
    """Convert a Task document to a plain dict for API/MCP responses."""
    return {
        "id": str(t.id),
        "project_id": t.project_id,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "assignee_id": t.assignee_id,
        "parent_task_id": t.parent_task_id,
        "blocks": list(t.blocks),
        "blocked_by": list(t.blocked_by),
        "task_type": t.task_type,
        "decision_context": (
            {
                "background": t.decision_context.background,
                "decision_point": t.decision_context.decision_point,
                "options": [
                    {"label": o.label, "description": o.description}
                    for o in t.decision_context.options
                ],
                "recommendation": t.decision_context.recommendation,
            }
            if t.decision_context
            else None
        ),
        "tags": t.tags,
        "comments": [
            {
                "id": c.id,
                "content": c.content,
                "author_id": c.author_id,
                "author_name": c.author_name,
                "created_at": c.created_at.isoformat(),
            }
            for c in t.comments
        ],
        "attachments": [
            {
                "id": a.id,
                "filename": a.filename,
                "content_type": a.content_type,
                "size": a.size,
                "created_at": a.created_at.isoformat(),
            }
            for a in t.attachments
        ],
        "created_by": t.created_by,
        "completion_report": t.completion_report,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "archived": t.archived,
        "needs_detail": t.needs_detail,
        "approved": t.approved,
        "sort_order": t.sort_order,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def project_to_dict(p: Project) -> dict:
    """Convert a Project document to a plain dict for API/MCP responses."""
    remote = getattr(p, "remote", None)
    remote_dict: dict | None = None
    if remote is not None:
        remote_dict = {
            "agent_id": remote.agent_id,
            "remote_path": remote.remote_path,
            "label": remote.label,
            "updated_at": remote.updated_at.isoformat(),
        }
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "color": p.color,
        "status": p.status,
        "is_locked": p.is_locked,
        "sort_order": p.sort_order,
        "hidden": getattr(p, "hidden", False),
        "remote": remote_dict,
        "members": [
            {"user_id": m.user_id, "role": m.role, "joined_at": m.joined_at.isoformat()}
            for m in p.members
        ],
        "created_by": str(p.created_by.ref.id) if hasattr(p.created_by, "ref") else str(p.created_by.id) if hasattr(p.created_by, "id") else str(p.created_by),
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def document_to_dict(d: ProjectDocument) -> dict:
    """Convert a ProjectDocument to a plain dict for API/MCP responses."""
    return {
        "id": str(d.id),
        "project_id": d.project_id,
        "title": d.title,
        "content": d.content,
        "tags": d.tags,
        "category": d.category,
        "version": d.version,
        "sort_order": d.sort_order,
        "created_by": d.created_by,
        "created_at": d.created_at.isoformat(),
        "updated_at": d.updated_at.isoformat(),
    }


def document_version_to_dict(v: DocumentVersion) -> dict:
    """Convert a DocumentVersion to a plain dict for API/MCP responses."""
    return {
        "id": str(v.id),
        "document_id": v.document_id,
        "version": v.version,
        "title": v.title,
        "content": v.content,
        "tags": v.tags,
        "category": v.category,
        "changed_by": v.changed_by,
        "task_id": v.task_id,
        "change_summary": v.change_summary,
        "created_at": v.created_at.isoformat(),
    }


def document_version_summary(v: DocumentVersion) -> dict:
    """Convert a DocumentVersion to a summary dict (without content)."""
    return {
        "id": str(v.id),
        "document_id": v.document_id,
        "version": v.version,
        "title": v.title,
        "changed_by": v.changed_by,
        "task_id": v.task_id,
        "change_summary": v.change_summary,
        "created_at": v.created_at.isoformat(),
    }


def knowledge_to_dict(k: Knowledge) -> dict:
    """Convert a Knowledge document to a plain dict for API/MCP responses."""
    return {
        "id": str(k.id),
        "title": k.title,
        "content": k.content,
        "tags": k.tags,
        "category": k.category,
        "source": k.source,
        "created_by": k.created_by,
        "created_at": k.created_at.isoformat(),
        "updated_at": k.updated_at.isoformat(),
    }


def secret_to_dict(s: ProjectSecret) -> dict:
    """Convert a ProjectSecret to a plain dict for API/MCP responses.

    The value is **not** included — only the key name and metadata are returned.
    Callers that need the value should access ``s.value`` directly.
    """
    return {
        "id": str(s.id),
        "project_id": s.project_id,
        "key": s.key,
        "description": s.description,
        "created_by": s.created_by,
        "updated_by": s.updated_by,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _section_to_dict(s: DocSiteSection) -> dict:
    return {
        "title": s.title,
        "path": s.path,
        "children": [_section_to_dict(c) for c in s.children],
    }


def docsite_to_dict(s: DocSite) -> dict:
    """Convert a DocSite document to a plain dict for API/MCP responses."""
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "source_url": s.source_url,
        "page_count": s.page_count,
        "sections": [_section_to_dict(sec) for sec in s.sections],
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def docsite_summary(s: DocSite) -> dict:
    """Convert a DocSite to a summary dict (without sections tree)."""
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "source_url": s.source_url,
        "page_count": s.page_count,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def bookmark_to_dict(b: Bookmark) -> dict:
    """Convert a Bookmark document to a plain dict for API/MCP responses."""
    return {
        "id": str(b.id),
        "project_id": b.project_id,
        "url": b.url,
        "title": b.title,
        "description": b.description,
        "tags": b.tags,
        "collection_id": b.collection_id,
        "metadata": {
            "meta_title": b.metadata.meta_title,
            "meta_description": b.metadata.meta_description,
            "favicon_url": b.metadata.favicon_url,
            "og_image_url": b.metadata.og_image_url,
            "site_name": b.metadata.site_name,
            "author": b.metadata.author,
            "published_date": b.metadata.published_date,
        },
        "clip_status": b.clip_status,
        "clip_content": b.clip_content,
        "clip_error": b.clip_error,
        "thumbnail_path": b.thumbnail_path,
        "is_starred": b.is_starred,
        "sort_order": b.sort_order,
        "created_by": b.created_by,
        "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat(),
    }


def bookmark_summary(b: Bookmark) -> dict:
    """Convert a Bookmark to a summary dict (without clip_content)."""
    return {
        "id": str(b.id),
        "project_id": b.project_id,
        "url": b.url,
        "title": b.title,
        "description": b.description,
        "tags": b.tags,
        "collection_id": b.collection_id,
        "metadata": {
            "meta_title": b.metadata.meta_title,
            "meta_description": b.metadata.meta_description,
            "favicon_url": b.metadata.favicon_url,
            "og_image_url": b.metadata.og_image_url,
            "site_name": b.metadata.site_name,
            "author": b.metadata.author,
            "published_date": b.metadata.published_date,
        },
        "clip_status": b.clip_status,
        "clip_error": b.clip_error,
        "thumbnail_path": b.thumbnail_path,
        "is_starred": b.is_starred,
        "sort_order": b.sort_order,
        "created_by": b.created_by,
        "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat(),
    }


def bookmark_collection_to_dict(c: BookmarkCollection) -> dict:
    """Convert a BookmarkCollection to a plain dict for API/MCP responses."""
    return {
        "id": str(c.id),
        "project_id": c.project_id,
        "name": c.name,
        "description": c.description,
        "icon": c.icon,
        "color": c.color,
        "sort_order": c.sort_order,
        "created_by": c.created_by,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def docpage_to_dict(p: DocPage) -> dict:
    """Convert a DocPage document to a plain dict for API/MCP responses."""
    return {
        "id": str(p.id),
        "site_id": p.site_id,
        "path": p.path,
        "title": p.title,
        "content": p.content,
        "sort_order": p.sort_order,
        "created_at": p.created_at.isoformat(),
    }
