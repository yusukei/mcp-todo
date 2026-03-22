from ..api_client import backend_request
from ..auth import authenticate
from ..server import mcp


@mcp.tool()
async def list_projects() -> list[dict]:
    """アクセス可能なプロジェクト一覧を取得する。"""
    key_info = await authenticate()
    scopes = key_info["project_scopes"]
    params = {"project_scopes": ",".join(scopes)} if scopes else {}
    return await backend_request("GET", "/projects", params=params)


@mcp.tool()
async def get_project(project_id: str) -> dict:
    """プロジェクトの詳細情報を取得する。

    Args:
        project_id: プロジェクトID
    """
    key_info = await authenticate()
    from ..auth import check_project_access
    check_project_access(project_id, key_info["project_scopes"])
    return await backend_request("GET", f"/projects/{project_id}")


@mcp.tool()
async def get_project_summary(project_id: str) -> dict:
    """プロジェクトの進捗サマリ（ステータス別タスク数・完了率）を取得する。

    Args:
        project_id: プロジェクトID
    """
    key_info = await authenticate()
    from ..auth import check_project_access
    check_project_access(project_id, key_info["project_scopes"])
    return await backend_request("GET", f"/projects/{project_id}/summary")
