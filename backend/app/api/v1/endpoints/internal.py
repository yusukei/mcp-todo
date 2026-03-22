"""Internal endpoints for MCP container requests.

Authenticated via X-MCP-Internal-Secret header.
Returns aggregated data needed by MCP tools.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ....core.deps import verify_mcp_secret
from ....models import McpApiKey, Project, Task, User
from ....models.task import Comment, TaskPriority, TaskStatus
from ....core.security import hash_api_key
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(verify_mcp_secret)])


class ApiKeyRequest(BaseModel):
    key: str


@router.post("/auth/api-key")
async def resolve_api_key(body: ApiKeyRequest) -> dict:
    key_hash = hash_api_key(body.key)
    api_key = await McpApiKey.find_one(McpApiKey.key_hash == key_hash, McpApiKey.is_active == True)
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    now = datetime.now(UTC)
    if api_key.last_used_at is None or (now - api_key.last_used_at).total_seconds() > 60:
        api_key.last_used_at = now
        await api_key.save()

    return {
        "key_id": str(api_key.id),
        "project_scopes": api_key.project_scopes,
    }


@router.get("/projects")
async def list_projects(project_scopes: str = "") -> list[dict]:
    from ....api.v1.endpoints.projects import _project_dict
    from ....models.project import ProjectStatus

    scopes = [s for s in project_scopes.split(",") if s] if project_scopes else []
    query = Project.find(Project.status == ProjectStatus.active)
    if scopes:
        query = query.find({"_id": {"$in": scopes}})
    projects = await query.to_list()
    return [_project_dict(p) for p in projects]


@router.get("/projects/{project_id}")
async def get_project(project_id: str) -> dict:
    from ....api.v1.endpoints.projects import _project_dict

    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return _project_dict(project)


@router.get("/projects/{project_id}/summary")
async def get_project_summary(project_id: str) -> dict:
    project = await Project.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

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


@router.get("/projects/{project_id}/tasks")
async def list_tasks(
    project_id: str,
    task_status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict

    query = Task.find(Task.project_id == project_id, Task.is_deleted == False)
    if task_status:
        query = query.find(Task.status == task_status)
    if priority:
        query = query.find(Task.priority == priority)
    if assignee_id:
        query = query.find(Task.assignee_id == assignee_id)
    if tag:
        query = query.find({"tags": tag})
    total = await query.count()
    tasks = await query.sort(+Task.sort_order, +Task.created_at).skip(skip).limit(limit).to_list()
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


class InternalAddCommentRequest(BaseModel):
    content: str
    author_name: str = "Claude"


class InternalCreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.medium
    status: TaskStatus = TaskStatus.todo
    due_date: datetime | None = None
    assignee_id: str | None = None
    parent_task_id: str | None = None
    tags: list[str] = []
    created_by: str = "mcp"


class InternalUpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: datetime | None = None
    assignee_id: str | None = None
    tags: list[str] | None = None


class BatchUpdateItem(BaseModel):
    task_id: str
    title: str | None = None
    description: str | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    due_date: datetime | None = None
    assignee_id: str | None = None
    tags: list[str] | None = None


# ---------------------------------------------------------------------------
# Static /tasks/ routes must come BEFORE /tasks/{task_id} to avoid capture
# ---------------------------------------------------------------------------

@router.get("/tasks/search")
async def search_tasks(
    q: str,
    project_ids: str = "",
    task_status: TaskStatus | None = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict

    regex_filter = {
        "$or": [
            {"title": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
        ]
    }
    filters: dict = {**regex_filter, "is_deleted": False}

    scopes = [s for s in project_ids.split(",") if s] if project_ids else []
    if scopes:
        filters["project_id"] = {"$in": scopes}
    if task_status:
        filters["status"] = task_status

    query = Task.find(filters)
    total = await query.count()
    tasks = await query.skip(skip).limit(limit).to_list()
    return {"items": [_task_dict(t) for t in tasks], "total": total, "limit": limit, "skip": skip}


@router.patch("/tasks/batch")
async def batch_update_tasks(body: list[BatchUpdateItem]) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict
    from ....services.events import publish_event

    updated = []
    failed = []
    for item in body:
        try:
            task = await Task.get(item.task_id)
            if not task or task.is_deleted:
                failed.append({"task_id": item.task_id, "error": "Task not found"})
                continue

            updates = item.model_dump(exclude_unset=True, exclude={"task_id"})
            for field, value in updates.items():
                if field == "status":
                    new_status = TaskStatus(value)
                    if new_status == TaskStatus.done and task.status != TaskStatus.done:
                        task.completed_at = datetime.now(UTC)
                    elif new_status != TaskStatus.done:
                        task.completed_at = None
                    task.status = new_status
                else:
                    setattr(task, field, value)

            await task.save_updated()
            await publish_event(task.project_id, "task.updated", _task_dict(task))
            updated.append(_task_dict(task))
        except Exception as e:
            logger.warning("batch_update_tasks: failed to update task '%s': %s", item.task_id, e)
            failed.append({"task_id": item.task_id, "error": str(e)})
    return {"updated": updated, "failed": failed}


# ---------------------------------------------------------------------------
# Parameterized /tasks/{task_id} routes
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict

    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_dict(task)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: InternalUpdateTaskRequest) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict
    from ....services.events import publish_event

    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "status":
            new_status = TaskStatus(value)
            if new_status == TaskStatus.done and task.status != TaskStatus.done:
                task.completed_at = datetime.now(UTC)
            elif new_status != TaskStatus.done:
                task.completed_at = None
            task.status = new_status
        else:
            setattr(task, field, value)

    await task.save_updated()
    await publish_event(task.project_id, "task.updated", _task_dict(task))
    return _task_dict(task)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str) -> None:
    from ....services.events import publish_event

    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    task.is_deleted = True
    await task.save_updated()
    await publish_event(task.project_id, "task.deleted", {"id": task_id})


@router.post("/tasks/{task_id}/comments", status_code=201)
async def add_comment(task_id: str, body: InternalAddCommentRequest) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict
    from ....services.events import publish_event

    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    comment = Comment(content=body.content, author_id="mcp", author_name=body.author_name)
    task.comments.append(comment)
    await task.save_updated()
    await publish_event(task.project_id, "comment.added", {"task_id": task_id, "comment": {
        "id": comment.id, "content": comment.content,
        "author_id": comment.author_id, "author_name": comment.author_name,
        "created_at": comment.created_at.isoformat(),
    }})
    return _task_dict(task)


# ---------------------------------------------------------------------------
# Other endpoints
# ---------------------------------------------------------------------------

@router.get("/users")
async def list_users() -> list[dict]:
    users = await User.find(User.is_active == True).to_list()
    return [{"id": str(u.id), "name": u.name, "email": u.email} for u in users]


@router.post("/projects/{project_id}/tasks")
async def create_task(project_id: str, body: InternalCreateTaskRequest) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict
    from ....services.events import publish_event

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
        created_by=body.created_by,
    )
    await task.insert()
    await publish_event(project_id, "task.created", _task_dict(task))
    return _task_dict(task)


@router.post("/projects/{project_id}/tasks/batch")
async def batch_create_tasks(project_id: str, body: list[InternalCreateTaskRequest]) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict
    from ....services.events import publish_event

    created = []
    failed = []
    for item in body:
        try:
            task = Task(
                project_id=project_id,
                title=item.title,
                description=item.description,
                priority=item.priority,
                status=item.status,
                due_date=item.due_date,
                assignee_id=item.assignee_id,
                parent_task_id=item.parent_task_id,
                tags=item.tags,
                created_by=item.created_by,
            )
            await task.insert()
            await publish_event(project_id, "task.created", _task_dict(task))
            created.append(_task_dict(task))
        except Exception as e:
            logger.warning("batch_create_tasks: failed to create task '%s': %s", item.title, e)
            failed.append({"title": item.title, "error": str(e)})
    return {"created": created, "failed": failed}
