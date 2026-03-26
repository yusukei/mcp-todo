from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Project, User
from ....models.project import MemberRole, ProjectMember, ProjectStatus
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


# ── Helpers ──────────────────────────────────────────────────


async def _check_project_access(project_id: str, user: User) -> Project:
    """Return project if user is admin or member; raise 403 otherwise."""
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
async def list_projects(user: User = Depends(get_current_user)) -> list[dict]:
    if user.is_admin:
        projects = await Project.find(Project.status == ProjectStatus.active).to_list()
    else:
        projects = await Project.find(
            Project.status == ProjectStatus.active,
            Project.members.user_id == str(user.id),
        ).to_list()
    return [_project_dict(p) for p in projects]


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
