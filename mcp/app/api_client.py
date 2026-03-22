"""Backend API クライアント。

MCP tools から Backend の /api/v1/internal/* エンドポイントを呼び出す。
X-MCP-Internal-Secret ヘッダーで内部認証。
"""

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

_INTERNAL_HEADERS = {
    "X-MCP-Internal-Secret": settings.MCP_INTERNAL_SECRET,
    "Content-Type": "application/json",
}


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


async def backend_request(method: str, path: str, **kwargs) -> dict | list:
    client = _get_client()
    resp = await client.request(method, f"/api/v1/internal{path}", **kwargs)
    resp.raise_for_status()
    return resp.json()
