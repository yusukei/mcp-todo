"""MCP ツール使用状況計測のデータモデル.

ハイブリッド方式 (案C) を採用:
- McpToolUsageBucket: 全呼び出しを time-bucket で集計 (call_count/error_count/duration_sum)
- McpToolCallEvent: エラー / スローコール / サンプリング対象のみ個別記録

注意: フィールド名は `call_count`. Beanie `Document.count` クラスメソッドと衝突するため
`count` という名前は使えない。

詳細はプロジェクトドキュメント "MCP サーバー仕様" / "データモデル仕様" を参照。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pymongo
from beanie import Document
from pydantic import Field

EventReason = Literal["error", "slow", "sampled"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class McpToolUsageBucket(Document):
    """`tool × api_key_id × hour` の集計バケット.

    各呼び出しは `$inc` の upsert 1回で更新される。
    複合 unique index `(tool_name, api_key_id, hour)` が upsert キー。
    """

    tool_name: str
    api_key_id: str | None = None
    hour: datetime  # UTC, minute=0 second=0 microsecond=0 に正規化

    call_count: int = 0
    error_count: int = 0
    duration_ms_sum: int = 0
    duration_ms_max: int = 0
    arg_size_sum: int = 0

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "mcp_tool_usage_buckets"
        indexes = [
            # upsert キー (unique)
            pymongo.IndexModel(
                [("tool_name", 1), ("api_key_id", 1), ("hour", 1)],
                unique=True,
                name="uniq_tool_key_hour",
            ),
            # ツール別時系列クエリ
            pymongo.IndexModel(
                [("tool_name", 1), ("hour", -1)], name="tool_hour"
            ),
            # 全ツール時系列 + TTL
            pymongo.IndexModel(
                [("hour", -1)], name="hour_desc"
            ),
            pymongo.IndexModel(
                [("hour", 1)],
                name="hour_ttl",
                expireAfterSeconds=60 * 60 * 24 * 90,  # 90 days
            ),
            # キー別利用状況
            pymongo.IndexModel(
                [("api_key_id", 1), ("hour", -1)], name="key_hour"
            ),
        ]


class McpToolCallEvent(Document):
    """エラー / スローコール / サンプリング対象の個別イベントログ.

    PII 保護のため、引数本文・エラーメッセージは保存しない。
    型情報とサイズのみ記録。
    """

    ts: datetime = Field(default_factory=_utcnow)
    tool_name: str
    api_key_id: str | None = None
    duration_ms: int
    success: bool
    error_class: str | None = None  # 例外型名のみ. メッセージ本文は保存しない
    arg_size_bytes: int = 0
    reason: EventReason

    class Settings:
        name = "mcp_tool_call_events"
        indexes = [
            # TTL: 14 days
            pymongo.IndexModel(
                [("ts", 1)],
                name="ts_ttl",
                expireAfterSeconds=60 * 60 * 24 * 14,
            ),
            pymongo.IndexModel(
                [("tool_name", 1), ("ts", -1)], name="tool_ts"
            ),
            pymongo.IndexModel(
                [("success", 1), ("ts", -1)], name="success_ts"
            ),
        ]
