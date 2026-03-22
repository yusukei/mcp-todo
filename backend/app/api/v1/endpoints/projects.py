from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ....core.deps import get_admin_user, get_current_user
from ....core.validators import valid_object_id
from ....models import Project, User
from ....models.project import ProjectMember, ProjectStatus
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


class AddMemberRequest(BaseModel):
    user_id: str


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
async def create_project(body: CreateProjectRequest, admin: User = Depends(get_admin_user)) -> dict:
    project = Project(
        name=body.name,
        description=body.description,
        color=body.color,
        created_by=admin,
        members=[ProjectMember(user_id=str(admin.id))],
    )
    await project.insert()
    return _project_dict(project)


@router.get("/{project_id}")
async def get_project(project_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return _project_dict(project)


@router.patch("/{project_id}")
async def update_project(
    project_id: str, body: UpdateProjectRequest, _: User = Depends(get_admin_user)
) -> dict:
    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.color is not None:
        project.color = body.color
    if body.status is not None:
        project.status = body.status
    await project.save_updated()
    return _project_dict(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, _: User = Depends(get_admin_user)) -> None:
    valid_object_id(project_id)
    from ....models import Task

    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    project.status = ProjectStatus.archived
    await project.save_updated()
    await Task.find(Task.project_id == project_id, Task.is_deleted == False).update(
        {"$set": {"is_deleted": True}}
    )


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(project_id: str, body: AddMemberRequest, _: User = Depends(get_admin_user)) -> dict:
    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.has_member(body.user_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member")
    project.members.append(ProjectMember(user_id=body.user_id))
    await project.save_updated()
    return _project_dict(project)


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(project_id: str, user_id: str, _: User = Depends(get_admin_user)) -> None:
    valid_object_id(project_id)
    valid_object_id(user_id)
    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    project.members = [m for m in project.members if m.user_id != user_id]
    await project.save_updated()


@router.get("/{project_id}/summary")
async def get_summary(project_id: str, user: User = Depends(get_current_user)) -> dict:
    valid_object_id(project_id)
    from ....models import Task
    from ....models.task import TaskStatus

    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")

    tasks = await Task.find(Task.project_id == project_id, Task.is_deleted == False).to_list()
    counts = {s: 0 for s in TaskStatus}
    for t in tasks:
        counts[t.status] += 1

    return {
        "project_id": project_id,
        "total": len(tasks),
        "by_status": {k: v for k, v in counts.items()},
        "completion_rate": round(counts[TaskStatus.done] / len(tasks) * 100, 1) if tasks else 0,
    }
