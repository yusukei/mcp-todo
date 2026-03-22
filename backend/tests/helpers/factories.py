"""テスト用データ生成ヘルパー"""

from datetime import UTC, datetime

from app.core.security import hash_password
from app.models import Project, Task, User
from app.models.project import ProjectMember
from app.models.task import TaskPriority, TaskStatus
from app.models.user import AuthType


async def make_admin_user(
    email: str = "admin@test.com",
    password: str = "adminpass",
    name: str = "Admin User",
) -> User:
    user = User(
        email=email,
        name=name,
        auth_type=AuthType.admin,
        password_hash=hash_password(password),
        is_admin=True,
        is_active=True,
    )
    await user.insert()
    return user


async def make_regular_user(
    email: str = "user@test.com",
    name: str = "Regular User",
    is_active: bool = True,
) -> User:
    user = User(
        email=email,
        name=name,
        auth_type=AuthType.google,
        is_admin=False,
        is_active=is_active,
    )
    await user.insert()
    return user


async def make_project(
    created_by: User,
    members: list[User] | None = None,
    name: str = "Test Project",
) -> Project:
    member_list = [ProjectMember(user_id=str(created_by.id))]
    if members:
        for m in members:
            if str(m.id) != str(created_by.id):
                member_list.append(ProjectMember(user_id=str(m.id)))

    project = Project(
        name=name,
        description="Test description",
        color="#6366f1",
        created_by=created_by,
        members=member_list,
    )
    await project.insert()
    return project


async def make_task(
    project_id: str,
    created_by: User,
    title: str = "Test Task",
    status: TaskStatus = TaskStatus.todo,
    priority: TaskPriority = TaskPriority.medium,
    due_date: datetime | None = None,
    tags: list[str] | None = None,
    is_deleted: bool = False,
    archived: bool = False,
    needs_detail: bool = False,
    approved: bool = False,
    parent_task_id: str | None = None,
) -> Task:
    task = Task(
        project_id=project_id,
        title=title,
        description="",
        status=status,
        priority=priority,
        due_date=due_date,
        tags=tags or [],
        created_by=str(created_by.id),
        is_deleted=is_deleted,
        archived=archived,
        needs_detail=needs_detail,
        approved=approved,
        parent_task_id=parent_task_id,
    )
    await task.insert()
    return task
