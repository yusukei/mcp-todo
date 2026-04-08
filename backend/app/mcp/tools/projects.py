import logging

from fastmcp.exceptions import ToolError

from ...models import Project, Task, User
from ...models.project import ProjectMember, ProjectStatus
from ...models.task import TaskStatus
from ...services.events import publish_event
from ...services.serializers import project_to_dict as _project_dict
from ..auth import authenticate, check_project_access
from ..server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def list_projects() -> list[dict]:
    """List projects accessible to the authenticated user.

    Admin users see all active projects; non-admin users see only the
    projects whose ``members`` list contains them.
    """
    key_info = await authenticate()

    query = Project.find(Project.status == ProjectStatus.active)
    if not key_info.get("is_admin"):
        user_id = key_info.get("user_id")
        if not user_id:
            return []
        query = query.find({"members.user_id": user_id})
    projects = await query.to_list()
    return [_project_dict(p) for p in projects]


@mcp.tool()
async def get_project(project_id: str) -> dict:
    """Get detailed information about a project.

    Args:
        project_id: Project ID or project name
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    project = await Project.get(project_id)
    if not project or project.status == ProjectStatus.archived:
        raise ToolError("Project not found")
    return _project_dict(project)


@mcp.tool()
async def create_project(
    name: str,
    description: str = "",
    color: str = "#6366f1",
) -> dict:
    """Create a new project.

    Args:
        name: Project name
        description: Project description
        color: Project color (hex, e.g. #6366f1)
    """
    await authenticate()

    admin_user = await User.find_one(User.is_admin == True)  # noqa: E712
    if not admin_user:
        raise ToolError("No admin user found to set as project creator")

    project = Project(
        name=name,
        description=description,
        color=color,
        created_by=admin_user,
        members=[ProjectMember(user_id=str(admin_user.id))],
    )
    await project.insert()
    await publish_event(str(project.id), "project.created", _project_dict(project))
    return _project_dict(project)


@mcp.tool()
async def update_project(
    project_id: str,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    status: str | None = None,
    is_locked: bool | None = None,
) -> dict:
    """Update a project. Only provided fields are changed.

    Args:
        project_id: Project ID or project name
        name: New project name
        description: New project description
        color: New project color (hex)
        status: New project status (active / archived)
        is_locked: Lock or unlock the project. Locked projects reject task and document changes.
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    project = await Project.get(project_id)
    if not project or project.status == ProjectStatus.archived:
        raise ToolError("Project not found")

    VALID_STATUSES = {"active", "archived"}
    if status is not None and status not in VALID_STATUSES:
        raise ToolError(f"Invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}")

    if name is not None:
        project.name = name
    if description is not None:
        project.description = description
    if color is not None:
        project.color = color
    if status is not None:
        project.status = ProjectStatus(status)
    if is_locked is not None:
        project.is_locked = is_locked

    await project.save_updated()
    await publish_event(project_id, "project.updated", _project_dict(project))
    return _project_dict(project)


@mcp.tool()
async def delete_project(project_id: str) -> dict:
    """Archive a project (soft delete). Also soft-deletes all tasks in the project.

    Args:
        project_id: Project ID or project name
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    project = await Project.get(project_id)
    if not project or project.status == ProjectStatus.archived:
        raise ToolError("Project not found")

    project.status = ProjectStatus.archived
    await project.save_updated()

    await Task.find(
        Task.project_id == project_id, Task.is_deleted == False  # noqa: E712
    ).update({"$set": {"is_deleted": True}})

    await publish_event(project_id, "project.deleted", {"id": project_id})
    return {"success": True, "project_id": project_id}


@mcp.tool()
async def get_project_summary(project_id: str) -> dict:
    """Get project progress summary (task counts by status, completion rate).

    Args:
        project_id: Project ID or project name
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    project = await Project.get(project_id)
    if not project or project.status == ProjectStatus.archived:
        raise ToolError("Project not found")

    pipeline = [
        {"$match": {"project_id": project_id, "is_deleted": False}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    results = await Task.get_motor_collection().aggregate(pipeline).to_list(length=None)

    counts = {s: 0 for s in TaskStatus}
    total = 0
    for doc in results:
        try:
            key = TaskStatus(doc["_id"])
        except ValueError:
            continue
        counts[key] = doc["count"]
        total += doc["count"]

    return {
        "project_id": project_id,
        "total": total,
        "by_status": {k: v for k, v in counts.items()},
        "completion_rate": round(counts[TaskStatus.done] / total * 100, 1) if total else 0,
    }


# ---------------------------------------------------------------------------
# Project name → ID resolver
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402

_project_cache: dict[str, tuple[str, float]] = {}  # name -> (id, expiry)
_PROJECT_CACHE_TTL = 300  # 5 minutes


async def _check_project_not_locked(project_id: str) -> None:
    """Raise ToolError if the project is locked or archived."""
    project = await Project.get(project_id)
    if not project or project.status == ProjectStatus.archived:
        raise ToolError("Project not found")
    if project.is_locked:
        raise ToolError("Project is locked. Unlock it before making changes.")


async def _resolve_project_id(project_id: str) -> str:
    """Resolve a project name to its ObjectId. Pass-through if already an ObjectId."""
    if len(project_id) == 24:
        try:
            int(project_id, 16)
            return project_id
        except ValueError:
            pass

    now = _time.monotonic()
    cached = _project_cache.get(project_id)
    if cached and cached[1] > now:
        return cached[0]

    project = await Project.find_one(
        Project.name == project_id, Project.status == ProjectStatus.active
    )
    if project:
        pid = str(project.id)
        _project_cache[project_id] = (pid, now + _PROJECT_CACHE_TTL)
        return pid

    raise ToolError(f"Project not found: {project_id}")
