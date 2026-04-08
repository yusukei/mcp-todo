"""MCP ツール使用状況の集計エンドポイント.

タスク 69d5b9f58e61d9be531aa532。
spec "MCP サーバー仕様 > REST API（集計エンドポイント）" を参照。

すべて管理者認証必須。
レスポンスフィールド名は `count` を維持 (フロントとの契約) するが、
内部の Mongo フィールドは `call_count` (Beanie の `Document.count` と衝突するため)。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from ....core.deps import get_admin_user
from ....mcp.server import mcp
from ....models import McpToolCallEvent, McpToolUsageBucket, User

router = APIRouter(prefix="/mcp/usage", tags=["mcp-usage"])


async def _registered_tool_names() -> list[str]:
    """FastMCP インスタンスに登録されているツール名一覧.

    `mcp.list_tools()` は middleware を経由してしまうため、
    `run_middleware=False` で素のリストを取得する。
    """
    try:
        tools = await mcp.list_tools(run_middleware=False)
    except Exception:
        return []
    names: list[str] = []
    for t in tools:
        name = getattr(t, "name", None)
        if name:
            names.append(name)
    return sorted(set(names))


@router.get("/summary")
async def usage_summary(
    days: int = Query(30, ge=1, le=365),
    _: User = Depends(get_admin_user),
) -> dict:
    """ツール別の総呼び出し数 / エラー率 / 平均応答時間 を返す.

    過去 `days` 日のバケットを集計し、未呼び出しツールも 0 件で含める。
    """
    since = datetime.now(UTC) - timedelta(days=days)

    pipeline = [
        {"$match": {"hour": {"$gte": since}}},
        {
            "$group": {
                "_id": "$tool_name",
                "count": {"$sum": "$call_count"},
                "error_count": {"$sum": "$error_count"},
                "duration_ms_sum": {"$sum": "$duration_ms_sum"},
                "duration_ms_max": {"$max": "$duration_ms_max"},
                "arg_size_sum": {"$sum": "$arg_size_sum"},
            }
        },
    ]
    rows = await McpToolUsageBucket.get_motor_collection().aggregate(pipeline).to_list(length=None)

    by_tool: dict[str, dict] = {}
    for row in rows:
        count = int(row.get("count", 0)) or 0
        errors = int(row.get("error_count", 0)) or 0
        d_sum = int(row.get("duration_ms_sum", 0)) or 0
        by_tool[row["_id"]] = {
            "tool_name": row["_id"],
            "count": count,
            "error_count": errors,
            "error_rate": (errors / count) if count else 0.0,
            "avg_duration_ms": (d_sum / count) if count else 0.0,
            "max_duration_ms": int(row.get("duration_ms_max", 0)) or 0,
            "arg_size_sum": int(row.get("arg_size_sum", 0)) or 0,
        }

    # 登録済みだが計測実績ゼロのツールも 0 行で含める
    for name in await _registered_tool_names():
        by_tool.setdefault(
            name,
            {
                "tool_name": name,
                "count": 0,
                "error_count": 0,
                "error_rate": 0.0,
                "avg_duration_ms": 0.0,
                "max_duration_ms": 0,
                "arg_size_sum": 0,
            },
        )

    items = sorted(by_tool.values(), key=lambda r: (-r["count"], r["tool_name"]))
    return {
        "since": since.isoformat(),
        "days": days,
        "total_calls": sum(r["count"] for r in items),
        "total_errors": sum(r["error_count"] for r in items),
        "tool_count": len(items),
        "items": items,
    }


@router.get("/unused")
async def usage_unused(
    days: int = Query(30, ge=1, le=365),
    _: User = Depends(get_admin_user),
) -> dict:
    """過去 N 日で呼び出し数がゼロのツール一覧 (=削除候補)."""
    since = datetime.now(UTC) - timedelta(days=days)
    used_names = await McpToolUsageBucket.get_motor_collection().distinct(
        "tool_name", {"hour": {"$gte": since}}
    )
    used = set(used_names)
    registered = await _registered_tool_names()
    unused = [name for name in registered if name not in used]
    return {
        "since": since.isoformat(),
        "days": days,
        "registered_count": len(registered),
        "used_count": len(used),
        "unused_count": len(unused),
        "unused": unused,
    }


@router.get("/timeseries")
async def usage_timeseries(
    tool: str = Query(..., min_length=1),
    days: int = Query(7, ge=1, le=90),
    _: User = Depends(get_admin_user),
) -> dict:
    """指定ツールの hour 粒度時系列データ."""
    since = datetime.now(UTC) - timedelta(days=days)
    pipeline = [
        {"$match": {"tool_name": tool, "hour": {"$gte": since}}},
        {
            "$group": {
                "_id": "$hour",
                "count": {"$sum": "$call_count"},
                "error_count": {"$sum": "$error_count"},
                "duration_ms_sum": {"$sum": "$duration_ms_sum"},
                "duration_ms_max": {"$max": "$duration_ms_max"},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    rows = await McpToolUsageBucket.get_motor_collection().aggregate(pipeline).to_list(length=None)
    points = [
        {
            "hour": (row["_id"].isoformat() if isinstance(row["_id"], datetime) else row["_id"]),
            "count": int(row.get("count", 0)) or 0,
            "error_count": int(row.get("error_count", 0)) or 0,
            "avg_duration_ms": (
                int(row["duration_ms_sum"]) / int(row["count"])
                if row.get("count")
                else 0.0
            ),
            "max_duration_ms": int(row.get("duration_ms_max", 0)) or 0,
        }
        for row in rows
    ]
    return {"tool": tool, "days": days, "points": points}


@router.get("/errors")
async def usage_errors(
    tool: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    only_errors: bool = Query(False),
    _: User = Depends(get_admin_user),
) -> dict:
    """個別イベントログ (エラー / スローコール / サンプリング) を返す."""
    query: dict = {}
    if tool:
        query["tool_name"] = tool
    if only_errors:
        query["success"] = False

    docs = (
        await McpToolCallEvent.find(query)
        .sort("-ts")
        .limit(limit)
        .to_list()
    )
    return {
        "items": [
            {
                "id": str(d.id),
                "ts": d.ts.isoformat(),
                "tool_name": d.tool_name,
                "api_key_id": d.api_key_id,
                "duration_ms": d.duration_ms,
                "success": d.success,
                "error_class": d.error_class,
                "arg_size_bytes": d.arg_size_bytes,
                "reason": d.reason,
            }
            for d in docs
        ]
    }


@router.get("/health")
async def usage_health(_: User = Depends(get_admin_user)) -> dict:
    """計測機能のヘルスチェック (有効/無効・サンプリング率など)."""
    from ....core.config import settings as _s

    bucket_count = await McpToolUsageBucket.get_motor_collection().estimated_document_count()
    event_count = await McpToolCallEvent.get_motor_collection().estimated_document_count()
    return {
        "enabled": _s.MCP_USAGE_TRACKING_ENABLED,
        "sampling_rate": _s.MCP_USAGE_SAMPLING_RATE,
        "slow_call_ms": _s.MCP_USAGE_SLOW_CALL_MS,
        "registered_tools": len(await _registered_tool_names()),
        "bucket_doc_count": bucket_count,
        "event_doc_count": event_count,
    }
