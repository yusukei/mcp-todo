import asyncio

from ..api_client import backend_request
from ..auth import authenticate, check_project_access
from ..server import mcp


async def _fetch_tasks(pid: str, params: dict | None = None) -> list:
    try:
        return await backend_request("GET", f"/projects/{pid}/tasks", params=params or {})
    except Exception:
        return []


@mcp.tool()
async def list_tasks(
    project_id: str,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    tag: str | None = None,
) -> list[dict]:
    """プロジェクト内のタスク一覧を取得する。

    Args:
        project_id: プロジェクトID
        status: フィルタ: todo / in_progress / in_review / done / cancelled
        priority: フィルタ: low / medium / high / urgent
        assignee_id: 担当者IDでフィルタ
        tag: タグ名でフィルタ
    """
    key_info = await authenticate()
    check_project_access(project_id, key_info["project_scopes"])
    params = {k: v for k, v in {"status": status, "priority": priority,
                                  "assignee_id": assignee_id, "tag": tag}.items() if v}
    return await backend_request("GET", f"/projects/{project_id}/tasks", params=params)


@mcp.tool()
async def get_task(task_id: str) -> dict:
    """タスクの詳細情報を取得する。

    Args:
        task_id: タスクID
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    return task


@mcp.tool()
async def create_task(
    project_id: str,
    title: str,
    description: str = "",
    priority: str = "medium",
    status: str = "todo",
    due_date: str | None = None,
    assignee_id: str | None = None,
    parent_task_id: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """新しいタスクを作成する。

    Args:
        project_id: プロジェクトID
        title: タスクタイトル
        description: タスクの詳細説明
        priority: 優先度 (low / medium / high / urgent)
        status: 初期ステータス (todo / in_progress / in_review / done / cancelled)
        due_date: 期限 (ISO 8601形式: 2025-12-31T00:00:00)
        assignee_id: 担当者のユーザID
        parent_task_id: 親タスクID（サブタスクの場合）
        tags: タグ名のリスト
    """
    key_info = await authenticate()
    check_project_access(project_id, key_info["project_scopes"])
    body = {
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
        "created_by": "mcp",
    }
    if due_date:
        body["due_date"] = due_date
    if assignee_id:
        body["assignee_id"] = assignee_id
    if parent_task_id:
        body["parent_task_id"] = parent_task_id
    if tags:
        body["tags"] = tags
    return await backend_request("POST", f"/projects/{project_id}/tasks", json=body)


@mcp.tool()
async def update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    due_date: str | None = None,
    assignee_id: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """タスクを更新する。Noneのフィールドは変更しない。

    Args:
        task_id: タスクID
        title: 新しいタイトル
        description: 新しい説明
        priority: 新しい優先度 (low / medium / high / urgent)
        status: 新しいステータス (todo / in_progress / in_review / done / cancelled)
        due_date: 新しい期限 (ISO 8601形式)
        assignee_id: 新しい担当者ID
        tags: 新しいタグリスト
    """
    key_info = await authenticate()
    # Validate enums
    VALID_STATUSES = {"todo", "in_progress", "in_review", "done", "cancelled"}
    VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}"}
    if priority is not None and priority not in VALID_PRIORITIES:
        return {"error": f"Invalid priority '{priority}'. Valid: {', '.join(sorted(VALID_PRIORITIES))}"}
    # Scope check: fetch task first
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    body = {k: v for k, v in {
        "title": title, "description": description, "priority": priority,
        "status": status, "due_date": due_date, "assignee_id": assignee_id,
        "tags": tags,
    }.items() if v is not None}
    return await backend_request("PATCH", f"/tasks/{task_id}", json=body)


@mcp.tool()
async def delete_task(task_id: str) -> dict:
    """タスクを削除する（論理削除）。

    Args:
        task_id: タスクID
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    await backend_request("DELETE", f"/tasks/{task_id}")
    return {"success": True, "task_id": task_id}


@mcp.tool()
async def complete_task(task_id: str) -> dict:
    """タスクを完了状態にする。

    Args:
        task_id: タスクID
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    return await backend_request("PATCH", f"/tasks/{task_id}", json={"status": "done"})


@mcp.tool()
async def add_comment(task_id: str, content: str) -> dict:
    """タスクにコメントを追加する。

    Args:
        task_id: タスクID
        content: コメント本文
    """
    key_info = await authenticate()
    task = await backend_request("GET", f"/tasks/{task_id}")
    check_project_access(task["project_id"], key_info["project_scopes"])
    return await backend_request("POST", f"/tasks/{task_id}/comments",
                                  json={"content": content, "author_name": "Claude"})


@mcp.tool()
async def search_tasks(
    query: str,
    project_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """タスクをキーワード検索する。

    Args:
        query: 検索キーワード
        project_id: 特定プロジェクト内のみ検索（省略時は全プロジェクト）
        status: ステータスでフィルタ
    """
    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    if project_id:
        check_project_access(project_id, scopes)
        project_ids = [project_id]
    else:
        if scopes:
            project_ids = scopes
        else:
            projects = await backend_request("GET", "/projects")
            project_ids = [p["id"] for p in projects]

    params = {"status": status} if status else {}
    task_lists = await asyncio.gather(*[_fetch_tasks(pid, params) for pid in project_ids])
    q = query.lower()
    results = []
    for tasks in task_lists:
        for t in tasks:
            if q in t["title"].lower() or q in t.get("description", "").lower():
                results.append(t)

    return results


@mcp.tool()
async def list_overdue_tasks(project_id: str | None = None) -> list[dict]:
    """期限切れのタスク一覧を取得する。

    Args:
        project_id: 特定プロジェクトのみ（省略時は全プロジェクト）
    """
    from datetime import UTC, datetime

    key_info = await authenticate()
    scopes = key_info["project_scopes"]

    if project_id:
        check_project_access(project_id, scopes)
        project_ids = [project_id]
    else:
        projects = await backend_request("GET", "/projects",
                                          params={"project_scopes": ",".join(scopes)} if scopes else {})
        project_ids = [p["id"] for p in projects]

    now = datetime.now(UTC).isoformat()
    task_lists = await asyncio.gather(*[_fetch_tasks(pid) for pid in project_ids])
    overdue = []
    for tasks in task_lists:
        for t in tasks:
            if (t.get("due_date") and t["due_date"] < now
                    and t["status"] not in ("done", "cancelled")):
                overdue.append(t)

    return sorted(overdue, key=lambda t: t["due_date"])


@mcp.tool()
async def list_users() -> list[dict]:
    """ユーザ一覧を取得する（担当者選択用）。"""
    await authenticate()
    return await backend_request("GET", "/users")
