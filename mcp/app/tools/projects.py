from ..api_client import backend_request
from ..auth import authenticate
from ..server import mcp


@mcp.tool()
async def list_projects() -> list[dict]:
    """List all accessible projects."""
    key_info = await authenticate()
    scopes = key_info["project_scopes"]
    params = {"project_scopes": ",".join(scopes)} if scopes else {}
    return await backend_request("GET", "/projects", params=params)


@mcp.tool()
async def get_project(project_id: str) -> dict:
    """Get detailed information about a project.

    Args:
        project_id: Project ID
    """
    key_info = await authenticate()
    from ..auth import check_project_access
    check_project_access(project_id, key_info["project_scopes"])
    return await backend_request("GET", f"/projects/{project_id}")


@mcp.tool()
async def get_project_summary(project_id: str) -> dict:
    """Get project progress summary (task counts by status, completion rate).

    Args:
        project_id: Project ID
    """
    key_info = await authenticate()
    from ..auth import check_project_access
    check_project_access(project_id, key_info["project_scopes"])
    return await backend_request("GET", f"/projects/{project_id}/summary")
