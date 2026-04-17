"""Cross-project live activity endpoint (Sprint 2 / S2-8).

Returns in-progress tasks across every project the caller can access, so the
front-end can render a horizontal feed like claude-task-viewer's Live Updates
panel without iterating ``list_tasks`` per project.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ....core.deps import get_current_user
from ....models import Project, Task, User
from ....models.project import ProjectStatus
from ....models.task import TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/live")
async def list_live_tasks(
    user: User = Depends(get_current_user),
    limit: int = 100,
) -> list[dict]:
    """Return all in-progress tasks the caller can see, ordered by recency.

    The response is a flat list (not wrapped in {"items": ...}) because this
    endpoint targets a streaming panel UI and pagination is not expected at
    this scale — in-progress tasks are naturally bounded. ``limit`` caps the
    return to avoid pathological cases with hundreds of concurrent agents.

    Response item shape::

        {
          "id": str,
          "title": str,
          "active_form": str | None,      # S2-2: what the task is doing now
          "assignee_id": str | None,
          "project_id": str,
          "project_name": str,
          "updated_at": str,              # ISO 8601
          "created_at": str,
        }
    """
    limit = max(1, min(limit, 500))

    # Build the project scope. Admins see every active project; regular users
    # see only projects where they are a listed member.
    if user.is_admin:
        project_query: dict = {"status": ProjectStatus.active}
    else:
        project_query = {
            "status": ProjectStatus.active,
            "members.user_id": str(user.id),
        }

    projects = await Project.find(project_query).to_list()
    project_ids = [str(p.id) for p in projects]
    project_name_by_id = {str(p.id): p.name for p in projects}

    if not project_ids:
        return []

    tasks = await Task.find(
        {
            "project_id": {"$in": project_ids},
            "is_deleted": False,
            "status": TaskStatus.in_progress,
        }
    ).sort(-Task.updated_at).limit(limit).to_list()

    return [
        {
            "id": str(t.id),
            "title": t.title,
            "active_form": t.active_form,
            "assignee_id": t.assignee_id,
            "project_id": t.project_id,
            "project_name": project_name_by_id.get(t.project_id, ""),
            "updated_at": t.updated_at.isoformat(),
            "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    ]
