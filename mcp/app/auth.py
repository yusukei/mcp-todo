"""API key authentication for MCP tools.

Tools call authenticate() to validate the X-API-Key header.
Rate limiting is managed on the Backend side.
"""

import hashlib
import logging
import time

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from .api_client import backend_request

logger = logging.getLogger(__name__)

# Auth cache: sha256(api_key) -> (result_dict, expiry_timestamp)
_auth_cache: dict[str, tuple[dict, float]] = {}
AUTH_CACHE_TTL = 300  # 5 minutes


class McpAuthError(ToolError):
    pass


async def authenticate() -> dict:
    """Validate the X-API-Key header and return key info.

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

    cache_key = hashlib.sha256(api_key.encode()).hexdigest()

    # Check cache
    cached = _auth_cache.get(cache_key)
    if cached is not None:
        result, expiry = cached
        if time.monotonic() < expiry:
            return result
        # Expired — remove stale entry
        del _auth_cache[cache_key]

    try:
        result = await backend_request("POST", "/auth/api-key", json={"key": api_key})
        _auth_cache[cache_key] = (result, time.monotonic() + AUTH_CACHE_TTL)
        return result
    except Exception as e:
        # Remove from cache on failure
        _auth_cache.pop(cache_key, None)
        logger.warning("API key validation failed: %s", e)
        raise McpAuthError("Invalid API key")


def check_project_access(project_id: str, scopes: list[str]) -> None:
    """Check project access. Empty scopes list means full access to all projects."""
    if scopes and project_id not in scopes:
        raise McpAuthError(f"No access to project {project_id}")
