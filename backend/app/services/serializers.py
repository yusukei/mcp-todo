"""Shared serialization helpers for Task and Project models.

Used by both the REST API endpoints and MCP tools to ensure
consistent response format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Project, Task


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
        "members": [
            {"user_id": m.user_id, "joined_at": m.joined_at.isoformat()}
            for m in p.members
        ],
        "created_by": str(p.created_by.ref.id) if hasattr(p.created_by, "ref") else str(p.created_by.id) if hasattr(p.created_by, "id") else str(p.created_by),
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }
