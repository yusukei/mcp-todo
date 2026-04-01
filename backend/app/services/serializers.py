"""Shared serialization helpers for Task and Project models.

Used by both the REST API endpoints and MCP tools to ensure
consistent response format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Project, Task
    from ..models.docsite import DocPage, DocSite, DocSiteSection
    from ..models.document import DocumentVersion, ProjectDocument
    from ..models.knowledge import Knowledge


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
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "color": p.color,
        "status": p.status,
        "is_locked": p.is_locked,
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
