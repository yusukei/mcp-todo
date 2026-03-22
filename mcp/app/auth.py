"""X-API-Key 認証。

tools内で authenticate() を呼び出してAPIキーを検証する。
レートリミットはBackend側で管理。
"""

import logging

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from .api_client import backend_request

logger = logging.getLogger(__name__)


class McpAuthError(ToolError):
    pass


async def authenticate() -> dict:
    """X-API-Keyを検証してキー情報を返す。

    Returns:
        {"key_id": str, "project_scopes": list[str]}
    """
    try:
        request = get_http_request()
    except RuntimeError:
        raise McpAuthError("HTTP request context unavailable")

    api_key = request.headers.get("x-api-key")
    if not api_key:
        raise McpAuthError("X-API-Key header required")

    try:
        result = await backend_request("GET", "/auth/api-key", params={"key": api_key})
        return result
    except Exception as e:
        logger.warning("API key validation failed: %s", e)
        raise McpAuthError("Invalid API key")


def check_project_access(project_id: str, scopes: list[str]) -> None:
    """project_scopesが空リスト=全プロジェクトアクセス可能。"""
    if scopes and project_id not in scopes:
        raise McpAuthError(f"No access to project {project_id}")
