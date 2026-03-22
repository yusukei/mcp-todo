from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ....core.deps import get_current_user
from ....models import Project, Task, User
from ....models.task import Comment, TaskPriority, TaskStatus
from ....services.events import publish_event

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.medium
    status: TaskStatus = TaskStatus.todo
    due_date: datetime | None = None
    assignee_id: str | None = None
    parent_task_id: str | None = None
    tags: list[str] = []


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: datetime | None = None
    assignee_id: str | None = None
    tags: list[str] | None = None


class AddCommentRequest(BaseModel):
    content: str


def _task_dict(t: Task) -> dict:
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
            {"id": c.id, "content": c.content, "author_id": c.author_id,
             "author_name": c.author_name, "created_at": c.created_at.isoformat()}
            for c in t.comments
        ],
        "created_by": t.created_by,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "sort_order": t.sort_order,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


async def _check_project_access(project_id: str, user: User) -> Project:
    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return project


@router.get("")
async def list_tasks(
    project_id: str,
    task_status: TaskStatus | None = Query(None, alias="status"),
    priority: TaskPriority | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
    user: User = Depends(get_current_user),
) -> list[dict]:
    await _check_project_access(project_id, user)

    query = Task.find(Task.project_id == project_id, Task.is_deleted == False)
    if task_status:
        query = query.find(Task.status == task_status)
    if priority:
        query = query.find(Task.priority == priority)
    if assignee_id:
        query = query.find(Task.assignee_id == assignee_id)
    if tag:
        query = query.find({"tags": tag})

    tasks = await query.sort(+Task.sort_order, +Task.created_at).to_list()
    return [_task_dict(t) for t in tasks]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    project_id: str, body: CreateTaskRequest, user: User = Depends(get_current_user)
) -> dict:
    await _check_project_access(project_id, user)

    task = Task(
        project_id=project_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        status=body.status,
        due_date=body.due_date,
        assignee_id=body.assignee_id,
        parent_task_id=body.parent_task_id,
        tags=body.tags,
        created_by=str(user.id),
    )
    await task.insert()
    await publish_event(project_id, "task.created", _task_dict(task))
    return _task_dict(task)


@router.get("/{task_id}")
async def get_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_dict(task)


@router.patch("/{task_id}")
async def update_task(
    project_id: str, task_id: str, body: UpdateTaskRequest, user: User = Depends(get_current_user)
) -> dict:
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if body.title is not None:
        task.title = body.title
    if body.description is not None:
        task.description = body.description
    if body.priority is not None:
        task.priority = body.priority
    if body.status is not None:
        if body.status == TaskStatus.done and task.status != TaskStatus.done:
            task.completed_at = datetime.now(UTC)
        elif body.status != TaskStatus.done:
            task.completed_at = None
        task.status = body.status
    if body.due_date is not None:
        task.due_date = body.due_date
    if body.assignee_id is not None:
        task.assignee_id = body.assignee_id
    if body.tags is not None:
        task.tags = body.tags

    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> None:
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.is_deleted = True
    await task.save_updated()
    await publish_event(project_id, "task.deleted", {"id": task_id})


@router.post("/{task_id}/complete")
async def complete_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.status = TaskStatus.done
    task.completed_at = datetime.now(UTC)
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.post("/{task_id}/reopen")
async def reopen_task(project_id: str, task_id: str, user: User = Depends(get_current_user)) -> dict:
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.status = TaskStatus.todo
    task.completed_at = None
    await task.save_updated()
    await publish_event(project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.post("/{task_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    project_id: str, task_id: str, body: AddCommentRequest, user: User = Depends(get_current_user)
) -> dict:
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    comment = Comment(content=body.content, author_id=str(user.id), author_name=user.name)
    task.comments.append(comment)
    await task.save_updated()
    await publish_event(project_id, "comment.added", {"task_id": task_id, "comment": {
        "id": comment.id, "content": comment.content,
        "author_id": comment.author_id, "author_name": comment.author_name,
        "created_at": comment.created_at.isoformat(),
    }})
    return _task_dict(task)


@router.delete("/{task_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    project_id: str, task_id: str, comment_id: str, user: User = Depends(get_current_user)
) -> None:
    await _check_project_access(project_id, user)
    task = await Task.get(task_id)
    if not task or task.project_id != project_id or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    comment = next((c for c in task.comments if c.id == comment_id), None)
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if comment.author_id != str(user.id) and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not comment author")

    task.comments = [c for c in task.comments if c.id != comment_id]
    await task.save_updated()
