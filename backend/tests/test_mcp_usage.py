"""MCP ツール使用状況計測のテスト.

タスク 69d5b9f58e61d9be531aa532。
- バケット upsert ロジック (`_record_bucket`)
- 個別イベント insert (`_record_event`)
- ミドルウェア on_call_tool の成功/失敗パス
- 集計 API (summary / unused / errors / health) の認可と動作
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.mcp.middleware.usage_tracking import (
    UsageTrackingMiddleware,
    _floor_to_hour,
    _record_bucket,
    _record_event,
)
from app.models import McpToolCallEvent, McpToolUsageBucket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_hour() -> datetime:
    return _floor_to_hour(datetime.now(UTC))


# ---------------------------------------------------------------------------
# _record_bucket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_bucket_inserts_new_doc():
    hour = _now_hour()
    await _record_bucket(
        tool_name="create_task",
        api_key_id="abc",
        hour=hour,
        duration_ms=120,
        success=True,
        arg_size=42,
    )
    docs = await McpToolUsageBucket.find({}).to_list()
    assert len(docs) == 1
    d = docs[0]
    assert d.tool_name == "create_task"
    assert d.api_key_id == "abc"
    assert d.call_count == 1
    assert d.error_count == 0
    assert d.duration_ms_sum == 120
    assert d.duration_ms_max == 120
    assert d.arg_size_sum == 42


@pytest.mark.asyncio
async def test_record_bucket_increments_existing():
    hour = _now_hour()
    for i, dur in enumerate([100, 200, 50]):
        await _record_bucket(
            tool_name="list_tasks",
            api_key_id="key1",
            hour=hour,
            duration_ms=dur,
            success=(i != 1),  # 2 番目だけ失敗
            arg_size=10,
        )
    docs = await McpToolUsageBucket.find({}).to_list()
    assert len(docs) == 1
    d = docs[0]
    assert d.call_count == 3
    assert d.error_count == 1
    assert d.duration_ms_sum == 350
    assert d.duration_ms_max == 200
    assert d.arg_size_sum == 30


@pytest.mark.asyncio
async def test_record_bucket_separates_by_key_and_hour():
    hour1 = _now_hour()
    hour2 = hour1 - timedelta(hours=1)
    await _record_bucket(tool_name="t", api_key_id="k1", hour=hour1, duration_ms=1, success=True, arg_size=0)
    await _record_bucket(tool_name="t", api_key_id="k2", hour=hour1, duration_ms=1, success=True, arg_size=0)
    await _record_bucket(tool_name="t", api_key_id="k1", hour=hour2, duration_ms=1, success=True, arg_size=0)
    assert await McpToolUsageBucket.find({}).count() == 3


# ---------------------------------------------------------------------------
# _record_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_event_persists_minimal_fields():
    await _record_event(
        tool_name="search_tasks",
        api_key_id="abc",
        duration_ms=3000,
        success=False,
        error_class="ValueError",
        arg_size=128,
        reason="error",
    )
    docs = await McpToolCallEvent.find({}).to_list()
    assert len(docs) == 1
    d = docs[0]
    assert d.tool_name == "search_tasks"
    assert d.success is False
    assert d.error_class == "ValueError"
    assert d.duration_ms == 3000
    assert d.reason == "error"


# ---------------------------------------------------------------------------
# UsageTrackingMiddleware
# ---------------------------------------------------------------------------


class _DummyMessage:
    def __init__(self, name: str, arguments: dict | None = None):
        self.name = name
        self.arguments = arguments or {}


class _DummyContext:
    def __init__(self, message):
        self.message = message
        self.method = "tools/call"
        self.type = "request"


@pytest.mark.asyncio
async def test_middleware_records_success_call(monkeypatch):
    # サンプリングを 100% にして必ず event が出るようにする
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "MCP_USAGE_SAMPLING_RATE", 1.0)

    mw = UsageTrackingMiddleware()
    ctx = _DummyContext(_DummyMessage("ping", {"a": 1}))

    async def _next(_c):
        return "ok"

    result = await mw.on_call_tool(ctx, _next)
    assert result == "ok"

    # asyncio.create_task で非同期書き込み → 完了を待つ
    import asyncio

    await asyncio.sleep(0.05)

    buckets = await McpToolUsageBucket.find({}).to_list()
    assert len(buckets) == 1
    assert buckets[0].tool_name == "ping"
    assert buckets[0].call_count == 1
    assert buckets[0].error_count == 0

    events = await McpToolCallEvent.find({}).to_list()
    assert len(events) == 1
    assert events[0].reason == "sampled"
    assert events[0].success is True


@pytest.mark.asyncio
async def test_middleware_records_failure_event(monkeypatch):
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "MCP_USAGE_SAMPLING_RATE", 0.0)

    mw = UsageTrackingMiddleware()
    ctx = _DummyContext(_DummyMessage("create_task", {"x": "y"}))

    async def _next(_c):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await mw.on_call_tool(ctx, _next)

    import asyncio

    await asyncio.sleep(0.05)

    buckets = await McpToolUsageBucket.find({}).to_list()
    assert len(buckets) == 1
    assert buckets[0].error_count == 1

    events = await McpToolCallEvent.find({}).to_list()
    assert len(events) == 1
    assert events[0].success is False
    assert events[0].error_class == "RuntimeError"
    assert events[0].reason == "error"


@pytest.mark.asyncio
async def test_middleware_disabled_short_circuits(monkeypatch):
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "MCP_USAGE_TRACKING_ENABLED", False)

    mw = UsageTrackingMiddleware()
    ctx = _DummyContext(_DummyMessage("ping"))

    async def _next(_c):
        return "ok"

    result = await mw.on_call_tool(ctx, _next)
    assert result == "ok"

    import asyncio

    await asyncio.sleep(0.05)

    assert await McpToolUsageBucket.find({}).count() == 0
    assert await McpToolCallEvent.find({}).count() == 0


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_buckets():
    """テスト用にバケットを直接挿入."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    docs = [
        McpToolUsageBucket(
            tool_name="hot_tool",
            api_key_id="k1",
            hour=now,
            call_count=100,
            error_count=2,
            duration_ms_sum=5000,
            duration_ms_max=300,
            arg_size_sum=10000,
        ),
        McpToolUsageBucket(
            tool_name="cold_tool",
            api_key_id="k1",
            hour=now - timedelta(hours=2),
            call_count=3,
            error_count=0,
            duration_ms_sum=90,
            duration_ms_max=40,
            arg_size_sum=300,
        ),
    ]
    for d in docs:
        await d.insert()
    return docs


@pytest.mark.asyncio
async def test_summary_requires_admin(client, user_headers):
    res = await client.get("/api/v1/mcp/usage/summary", headers=user_headers)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_summary_aggregates_buckets(client, admin_headers, seed_buckets):
    res = await client.get("/api/v1/mcp/usage/summary?days=30", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    by_name = {r["tool_name"]: r for r in body["items"]}
    assert by_name["hot_tool"]["count"] == 100
    assert by_name["hot_tool"]["error_count"] == 2
    assert by_name["hot_tool"]["error_rate"] == pytest.approx(0.02)
    assert by_name["hot_tool"]["avg_duration_ms"] == pytest.approx(50.0)
    assert by_name["cold_tool"]["count"] == 3
    assert body["total_calls"] >= 103


@pytest.mark.asyncio
async def test_unused_endpoint_returns_zero_call_tools(client, admin_headers, seed_buckets):
    res = await client.get("/api/v1/mcp/usage/unused?days=30", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    # 実 MCP インスタンスのツール一覧が空でも壊れない (registered_count=0 でも OK)
    assert "unused" in body
    assert isinstance(body["unused"], list)
    assert body["used_count"] >= 2  # hot_tool / cold_tool


@pytest.mark.asyncio
async def test_errors_endpoint_returns_recent_events(client, admin_headers):
    await McpToolCallEvent(
        tool_name="failing_tool",
        api_key_id="k1",
        duration_ms=4321,
        success=False,
        error_class="TimeoutError",
        arg_size_bytes=99,
        reason="error",
    ).insert()
    res = await client.get("/api/v1/mcp/usage/errors?only_errors=true", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["tool_name"] == "failing_tool"
    assert body["items"][0]["error_class"] == "TimeoutError"
    # PII 観点: error_class 以外の本文は保存されていない
    assert "error_message" not in body["items"][0]


@pytest.mark.asyncio
async def test_health_endpoint(client, admin_headers):
    res = await client.get("/api/v1/mcp/usage/health", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert "enabled" in body
    assert "sampling_rate" in body
    assert "slow_call_ms" in body
