"""MCP コンテナからの内部リクエスト用エンドポイント。

X-MCP-Internal-Secret ヘッダーで認証。
MCP tools が必要とするデータをまとめて返す。
"""

from fastapi import APIRouter, Depends, HTTPException, status

from ....core.deps import verify_mcp_secret
from ....models import McpApiKey, Project, Task, User
from ....models.task import TaskPriority, TaskStatus
from ....core.security import hash_api_key
from datetime import UTC, datetime

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(verify_mcp_secret)])


@router.get("/auth/api-key")
async def resolve_api_key(key: str) -> dict:
    key_hash = hash_api_key(key)
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


@router.get("/projects/{project_id}/tasks")
async def list_tasks(
    project_id: str,
    task_status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    assignee_id: str | None = None,
) -> list[dict]:
    from ....api.v1.endpoints.tasks import _task_dict

    query = Task.find(Task.project_id == project_id, Task.is_deleted == False)
    if task_status:
        query = query.find(Task.status == task_status)
    if priority:
        query = query.find(Task.priority == priority)
    if assignee_id:
        query = query.find(Task.assignee_id == assignee_id)
    tasks = await query.sort(+Task.sort_order, +Task.created_at).to_list()
    return [_task_dict(t) for t in tasks]


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict

    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_dict(task)


@router.post("/projects/{project_id}/tasks")
async def create_task(project_id: str, body: dict) -> dict:
    from ....api.v1.endpoints.tasks import _task_dict
    from ....models.task import TaskPriority, TaskStatus
    from ....services.events import publish_event

    task = Task(
        project_id=project_id,
        title=body["title"],
        description=body.get("description", ""),
        priority=TaskPriority(body.get("priority", "medium")),
        status=TaskStatus(body.get("status", "todo")),
        due_date=body.get("due_date"),
        assignee_id=body.get("assignee_id"),
        parent_task_id=body.get("parent_task_id"),
        tags=body.get("tags", []),
        created_by=body.get("created_by", "mcp"),
    )
    await task.insert()
    await publish_event(project_id, "task.created", _task_dict(task))
    return _task_dict(task)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: dict) -> dict:
    from datetime import UTC, datetime

    from ....api.v1.endpoints.tasks import _task_dict
    from ....models.task import TaskStatus
    from ....services.events import publish_event

    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    for field in ("title", "description", "assignee_id", "tags"):
        if field in body:
            setattr(task, field, body[field])
    if "priority" in body:
        from ....models.task import TaskPriority
        task.priority = TaskPriority(body["priority"])
    if "status" in body:
        new_status = TaskStatus(body["status"])
        if new_status == TaskStatus.done and task.status != TaskStatus.done:
            task.completed_at = datetime.now(UTC)
        elif new_status != TaskStatus.done:
            task.completed_at = None
        task.status = new_status
    if "due_date" in body:
        task.due_date = body["due_date"]

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


@router.get("/users")
async def list_users() -> list[dict]:
    users = await User.find(User.is_active == True).to_list()
    return [{"id": str(u.id), "name": u.name, "email": u.email} for u in users]
