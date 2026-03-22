"""Backend API client.

Calls Backend /api/v1/internal/* endpoints from MCP tools.
Authenticated via the X-MCP-Internal-Secret header.
"""

import asyncio
import logging

import httpx
from fastmcp.exceptions import ToolError

from .config import settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

_INTERNAL_HEADERS = {
    "X-MCP-Internal-Secret": settings.MCP_INTERNAL_SECRET,
    "Content-Type": "application/json",
}

MAX_RETRIES = 3
RETRY_DELAYS = [0.5, 1.0, 2.0]


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.BACKEND_URL,
            headers=_INTERNAL_HEADERS,
            timeout=30.0,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def backend_request(method: str, path: str, **kwargs) -> dict | list | None:
    client = _get_client()
    last_error: Exception | None = None
    url = f"/api/v1/internal{path}"

    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            if resp.status_code == 204:
                return None
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:
                    detail = str(e)
                raise ToolError(detail) from e
            last_error = e
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last_error = e

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAYS[attempt])

    raise last_error  # type: ignore[misc]
