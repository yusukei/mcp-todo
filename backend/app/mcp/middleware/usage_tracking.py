"""MCP ツール呼び出しの使用状況を計測する FastMCP ミドルウェア.

ハイブリッド方式 (案C):
- すべての呼び出しを `mcp_tool_usage_buckets` に時間バケット集計 ($inc upsert)
- エラー / スローコール / サンプリング対象のみ `mcp_tool_call_events` に詳細記録

設計は spec "MCP サーバー仕様 > ツール使用状況計測" を参照。
タスク 69d5b9f58e61d9be531aa532。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import UTC, datetime
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from ...core.config import settings
from ...core.security import hash_api_key
from ...models import McpToolCallEvent, McpToolUsageBucket

logger = logging.getLogger(__name__)


def _floor_to_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def _resolve_api_key_id() -> str | None:
    """ミドルウェアコンテキストから呼び出し元の識別子を解決する.

    優先順:
    1. X-API-Key ヘッダ → SHA256 ハッシュ (mcp_api_keys.key_hash と一致)
    2. OAuth Bearer トークンの claims["user_id"] → "user:<id>"
    3. None (未認証または取得失敗)

    認証ミドルウェアより後ろで動く前提だが、エラーにはしない。
    """
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
        api_key = request.headers.get("x-api-key")
        if api_key:
            return hash_api_key(api_key)
    except Exception:
        pass

    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
        if token is not None and hasattr(token, "claims"):
            claims = token.claims  # type: ignore[union-attr]
            if isinstance(claims, dict):
                user_id = claims.get("user_id")
                if user_id:
                    return f"user:{user_id}"
    except Exception:
        pass

    return None


def _arg_size_bytes(message: Any) -> int:
    """ツール呼び出しの引数サイズ (bytes) を概算する.

    PII 観点で本文は保存しない。サイズだけ。
    """
    args = getattr(message, "arguments", None)
    if args is None:
        return 0
    try:
        return len(json.dumps(args, default=str, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


async def _record_bucket(
    *,
    tool_name: str,
    api_key_id: str | None,
    hour: datetime,
    duration_ms: int,
    success: bool,
    arg_size: int,
) -> None:
    """`mcp_tool_usage_buckets` に集計を upsert する."""
    try:
        col = McpToolUsageBucket.get_motor_collection()
        now = datetime.now(UTC)
        await col.update_one(
            {"tool_name": tool_name, "api_key_id": api_key_id, "hour": hour},
            {
                "$inc": {
                    "call_count": 1,
                    "error_count": 0 if success else 1,
                    "duration_ms_sum": duration_ms,
                    "arg_size_sum": arg_size,
                },
                "$max": {"duration_ms_max": duration_ms},
                "$setOnInsert": {"created_at": now},
                "$set": {"updated_at": now},
            },
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("usage bucket upsert failed: %s", e)


async def _record_event(
    *,
    tool_name: str,
    api_key_id: str | None,
    duration_ms: int,
    success: bool,
    error_class: str | None,
    arg_size: int,
    reason: str,
) -> None:
    """`mcp_tool_call_events` に個別ログを insert する."""
    try:
        await McpToolCallEvent(
            tool_name=tool_name,
            api_key_id=api_key_id,
            duration_ms=duration_ms,
            success=success,
            error_class=error_class,
            arg_size_bytes=arg_size,
            reason=reason,  # type: ignore[arg-type]
        ).insert()
    except Exception as e:  # noqa: BLE001
        logger.warning("usage event insert failed: %s", e)


def _should_sample() -> bool:
    rate = settings.MCP_USAGE_SAMPLING_RATE
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    return random.random() < rate


class UsageTrackingMiddleware(Middleware):
    """FastMCP ミドルウェア: ツール呼び出しを計測する.

    例外は内部で握りつぶし、呼び出し本体には影響を与えない (fire-and-forget)。
    DB 書き込みは `asyncio.create_task` で非同期化し、応答パスをブロックしない。
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        if not settings.MCP_USAGE_TRACKING_ENABLED:
            return await call_next(context)

        tool_name = getattr(context.message, "name", None) or "<unknown>"
        api_key_id = _resolve_api_key_id()
        arg_size = _arg_size_bytes(context.message)
        started_ns = time.perf_counter_ns()
        success = True
        error_class: str | None = None

        try:
            return await call_next(context)
        except BaseException as exc:  # noqa: BLE001
            success = False
            error_class = type(exc).__name__
            raise
        finally:
            duration_ms = max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)
            now = datetime.now(UTC)
            hour = _floor_to_hour(now)

            # Schedule writes as background tasks so the response path is
            # never blocked. Failures are logged but never propagated.
            try:
                asyncio.create_task(
                    _record_bucket(
                        tool_name=tool_name,
                        api_key_id=api_key_id,
                        hour=hour,
                        duration_ms=int(duration_ms),
                        success=success,
                        arg_size=arg_size,
                    )
                )
            except RuntimeError:
                # No running event loop (shouldn't happen in async context)
                pass

            # Record an individual event when:
            #   - failure (always)
            #   - slow call (always)
            #   - sampled (probabilistic)
            slow = duration_ms > settings.MCP_USAGE_SLOW_CALL_MS
            if not success:
                reason = "error"
            elif slow:
                reason = "slow"
            elif _should_sample():
                reason = "sampled"
            else:
                reason = ""

            if reason:
                try:
                    asyncio.create_task(
                        _record_event(
                            tool_name=tool_name,
                            api_key_id=api_key_id,
                            duration_ms=int(duration_ms),
                            success=success,
                            error_class=error_class,
                            arg_size=arg_size,
                            reason=reason,
                        )
                    )
                except RuntimeError:
                    pass
