import asyncio
from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Project, User
from ....models.project import MemberRole, ProjectMember, ProjectRemoteBinding, ProjectStatus
from ....models.remote import RemoteAgent
from ....services.serializers import project_to_dict as _project_dict

router = APIRouter(prefix="/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str = Field(..., max_length=255)
    description: str = Field("", max_length=5000)
    color: str = Field("#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=5000)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    status: ProjectStatus | None = None
    is_locked: bool | None = None


class AddMemberRequest(BaseModel):
    user_id: str
    role: MemberRole = MemberRole.member


class UpdateMemberRequest(BaseModel):
    role: MemberRole


class ProjectRemoteBindingRequest(BaseModel):
    """Request body for PUT /projects/{id}/remote.

    Use DELETE /projects/{id}/remote to clear the binding instead of
    sending a null body — keeps the PUT semantics unambiguous.
    """

    agent_id: str
    remote_path: str = Field(..., min_length=1, max_length=1000)
    label: str = Field("", max_length=200)


# ── Helpers ──────────────────────────────────────────────────


async def _check_project_access(project_id: str, user: User) -> Project:
    """Return project if user is admin or member; raise 403 otherwise.

    Hidden projects (e.g. the singleton "Common" project) are accepted —
    they are intentionally hidden from listings but remain reachable for
    members so cross-cutting features like Chat / Bookmarks can use them.
    """
    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project or project.status == ProjectStatus.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return project


async def _check_owner_or_admin(project_id: str, user: User) -> Project:
    """Return project if user is admin or project owner; raise 403 otherwise."""
    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project or project.status == ProjectStatus.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.is_owner(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner or admin required")
    return project


# ── Endpoints ────────────────────────────────────────────────


@router.get("")
async def list_projects(
    user: User = Depends(get_current_user),
    include_hidden: bool = Query(
        False,
        description=(
            "Include hidden projects (the Common singleton). "
            "Used by the Workspaces / Chat / Bookmarks pages so they can "
            "still resolve the Common project."
        ),
    ),
) -> list[dict]:
    base_filters: list = [Project.status == ProjectStatus.active]
    if not user.is_admin:
        base_filters.append(Project.members.user_id == str(user.id))

    projects = await Project.find(*base_filters).sort("+sort_order", "+created_at").to_list()
    if not include_hidden:
        # Default behaviour: exclude hidden projects from sidebar/list views.
        projects = [p for p in projects if not getattr(p, "hidden", False)]
    return [_project_dict(p) for p in projects]


@router.get("/common")
async def get_common_project(user: User = Depends(get_current_user)) -> dict:
    """Return the singleton hidden Common project.

    The Common project hosts cross-cutting features (Chat, Bookmarks) that
    historically lived inside an arbitrary project. By being hidden it does
    not appear in the project sidebar but remains a real Project document
    so existing code paths (bookmark project_id, chat session
    project_id, embedded remote binding) keep working unchanged.

    Returns 404 if the Common project has not been provisioned yet
    (run `python -m app.cli setup-common-project`).
    """
    project = await Project.find_one(
        {"hidden": True, "status": ProjectStatus.active.value},
    )
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Common project not provisioned",
        )
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return _project_dict(project)


class ReorderProjectsRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)


@router.post("/reorder")
async def reorder_projects(
    body: ReorderProjectsRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Reorder projects. Admin only."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")

    try:
        oids = [ObjectId(pid) for pid in body.ids]
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid project ID")

    items = await Project.find(
        {"_id": {"$in": oids}, "status": ProjectStatus.active},
    ).to_list()
    item_map = {str(p.id): p for p in items}

    updates = []
    for i, pid in enumerate(body.ids):
        p = item_map.get(pid)
        if p and p.sort_order != i:
            p.sort_order = i
            updates.append(p.save())
    if updates:
        await asyncio.gather(*updates)

    return {"reordered": len(updates)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(body: CreateProjectRequest, user: User = Depends(get_current_user)) -> dict:
    project = Project(
        name=body.name,
        description=body.description,
        color=body.color,
        created_by=user,
        members=[ProjectMember(user_id=str(user.id), role=MemberRole.owner)],
    )
    await project.insert()
    return _project_dict(project)


@router.get("/{project_id}")
async def get_project(project_id: str, user: User = Depends(get_current_user)) -> dict:
    project = await _check_project_access(project_id, user)
    return _project_dict(project)


@router.patch("/{project_id}")
async def update_project(
    project_id: str, body: UpdateProjectRequest, user: User = Depends(get_current_user)
) -> dict:
    project = await _check_owner_or_admin(project_id, user)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.color is not None:
        project.color = body.color
    if body.status is not None:
        project.status = body.status
    if body.is_locked is not None:
        project.is_locked = body.is_locked
    await project.save_updated()
    return _project_dict(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user: User = Depends(get_current_user)) -> None:
    import shutil
    from pathlib import Path

    from ....models import Task

    project = await _check_owner_or_admin(project_id, user)

    # Collect task IDs for attachment cleanup before soft-deleting
    tasks = await Task.find(
        Task.project_id == project_id, Task.is_deleted == False  # noqa: E712
    ).to_list()
    uploads_dir = Path(__file__).resolve().parents[4] / "uploads"
    for task in tasks:
        task_upload_dir = uploads_dir / str(task.id)
        if task_upload_dir.exists():
            shutil.rmtree(task_upload_dir, ignore_errors=True)

    project.status = ProjectStatus.archived
    await project.save_updated()
    await Task.find(Task.project_id == project_id, Task.is_deleted == False).update(
        {"$set": {"is_deleted": True}}
    )


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: str, body: AddMemberRequest, user: User = Depends(get_current_user)
) -> dict:
    project = await _check_owner_or_admin(project_id, user)
    if project.has_member(body.user_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member")
    project.members.append(ProjectMember(user_id=body.user_id, role=body.role))
    await project.save_updated()
    return _project_dict(project)


@router.patch("/{project_id}/members/{user_id}")
async def update_member_role(
    project_id: str, user_id: str, body: UpdateMemberRequest, user: User = Depends(get_current_user)
) -> dict:
    project = await _check_owner_or_admin(project_id, user)
    valid_object_id(user_id)
    member = project.get_member(user_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.role == body.role:
        return _project_dict(project)
    # Prevent demoting the last owner
    if member.role == MemberRole.owner and body.role != MemberRole.owner:
        owner_count = sum(1 for m in project.members if m.role == MemberRole.owner)
        if owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Cannot demote the last owner"
            )
    member.role = body.role
    await project.save_updated()
    return _project_dict(project)


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: str, user_id: str, user: User = Depends(get_current_user)
) -> None:
    project = await _check_owner_or_admin(project_id, user)
    valid_object_id(user_id)
    member = project.get_member(user_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.role == MemberRole.owner:
        owner_count = sum(1 for m in project.members if m.role == MemberRole.owner)
        if owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Cannot remove the last owner"
            )
    project.members = [m for m in project.members if m.user_id != user_id]
    await project.save_updated()


@router.put("/{project_id}/remote")
async def set_project_remote(
    project_id: str,
    body: ProjectRemoteBindingRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Bind this project to a remote agent + directory.

    Replaces any existing binding (one project = one remote). Only the
    project owner or an admin may configure this. The supplied
    ``agent_id`` must reference an agent owned by the calling user
    (admins can reference any agent).
    """
    project = await _check_owner_or_admin(project_id, user)
    valid_object_id(body.agent_id)

    agent = await RemoteAgent.get(body.agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    if not user.is_admin and agent.owner_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot bind to an agent you do not own",
        )

    project.remote = ProjectRemoteBinding(
        agent_id=body.agent_id,
        remote_path=body.remote_path,
        label=body.label,
        updated_at=datetime.now(UTC),
    )
    await project.save_updated()
    return _project_dict(project)


@router.delete("/{project_id}/remote")
async def clear_project_remote(
    project_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Clear this project's remote agent binding."""
    project = await _check_owner_or_admin(project_id, user)
    project.remote = None
    await project.save_updated()
    return _project_dict(project)


@router.get("/{project_id}/summary")
async def get_summary(project_id: str, user: User = Depends(get_current_user)) -> dict:
    from ....models import Task
    from ....models.task import TaskStatus

    project = await _check_project_access(project_id, user)

    pipeline = [
        {"$match": {"project_id": str(project.id), "is_deleted": False}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    results = await Task.get_motor_collection().aggregate(pipeline).to_list(length=None)

    counts = {s: 0 for s in TaskStatus}
    total = 0
    for doc in results:
        status_val = doc["_id"]
        try:
            key = TaskStatus(status_val)
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
