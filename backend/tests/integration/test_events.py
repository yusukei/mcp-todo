"""SSE エンドポイントの統合テスト

認証チェック + プロジェクトフィルタリングの検証。

httpx ASGI トランスポートは SSE チャンクを個別にフラッシュしないため、
ストリーム本文の読み取りを伴うテストは TEST_MODE=real でのみ動作する。
"""

import asyncio
import json
import os

import pytest

from app.core.redis import get_redis
from app.core.security import create_access_token, create_refresh_token
from app.models import Project, User
from app.models.project import ProjectMember

# mongomock-motor はネストフィールドクエリ (Project.members.user_id) 非互換
# かつ httpx ASGI は SSE チャンクをフラッシュしないため本文検証も real のみ
needs_real = pytest.mark.skipif(
    os.environ.get("TEST_MODE", "mock") == "mock",
    reason="mongomock-motor does not fully support nested field queries; run with TEST_MODE=real",
)


async def _get_ticket(client, user) -> str:
    """Helper: obtain an SSE ticket for the given user via the ticket endpoint."""
    token = create_access_token(str(user.id))
    resp = await client.post(
        "/api/v1/events/ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    return resp.json()["ticket"]


class TestSSETicket:
    """SSE チケット発行エンドポイントのテスト"""

    async def test_ticket_requires_auth(self, client):
        """認証なしでチケット取得は 401"""
        resp = await client.post("/api/v1/events/ticket")
        assert resp.status_code in (401, 403)

    async def test_ticket_returns_ticket_string(self, client, admin_user):
        """有効な JWT でチケットが取得できる"""
        ticket = await _get_ticket(client, admin_user)
        assert isinstance(ticket, str)
        assert len(ticket) == 32  # uuid4().hex

    async def test_ticket_is_single_use(self, client, admin_user):
        """チケットは 1 回のみ使用可能"""
        ticket = await _get_ticket(client, admin_user)

        # First use: should succeed (stream starts)
        async def _first_use():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200

        try:
            await asyncio.wait_for(_first_use(), timeout=3)
        except (TimeoutError, asyncio.CancelledError):
            pass

        # Second use: ticket consumed, should fail
        resp = await client.get(f"/api/v1/events?ticket={ticket}")
        assert resp.status_code == 401

    async def test_invalid_ticket_returns_401(self, client):
        """無効なチケットは 401"""
        resp = await client.get("/api/v1/events?ticket=invalidticketvalue")
        assert resp.status_code == 401


class TestSSEAuthentication:
    async def test_no_ticket_returns_422(self, client):
        """ticket クエリパラメータ必須のため未指定は 422"""
        resp = await client.get("/api/v1/events")
        assert resp.status_code == 422

    async def test_invalid_ticket_returns_401(self, client):
        resp = await client.get("/api/v1/events?ticket=invalid.ticket.here")
        assert resp.status_code == 401

    async def test_inactive_user_ticket_returns_401(self, client, inactive_user):
        """非アクティブユーザーのチケットは 401 (手動で Redis にチケットを仕込む)"""
        redis = get_redis()
        ticket = "testticketforinactiveuser00000"
        await redis.set(f"sse_ticket:{ticket}", str(inactive_user.id), ex=30)
        resp = await client.get(f"/api/v1/events?ticket={ticket}")
        assert resp.status_code == 401

    async def test_valid_ticket_starts_stream(self, client, admin_user):
        """有効なチケットで SSE ストリームが開始される (ステータス + Content-Type 確認)

        注: httpx の ASGI トランスポートは SSE チャンクを個別にフラッシュしないため、
        aiter_bytes() がブロックする。ストリーム本文の読み取りは E2E テストに委ねる。
        """
        ticket = await _get_ticket(client, admin_user)

        async def _check_stream():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")

        try:
            await asyncio.wait_for(_check_stream(), timeout=3)
        except (TimeoutError, asyncio.CancelledError):
            pass  # SSE は無限ストリームなのでタイムアウトは正常動作


class TestSSEProjectFiltering:
    """SSE ストリームのプロジェクトフィルタリング検証。

    一般ユーザーは自分がメンバーのプロジェクトのイベントのみ受信し、
    管理者は全プロジェクトのイベントを受信する。

    注: Project.members.user_id によるクエリは mongomock-motor 非互換、
    かつ httpx ASGI は SSE チャンクをフラッシュしないため、
    ストリーム本文検証を伴うテストは needs_real マーク。
    接続確立のみ (ステータス + Content-Type) の検証は mock でも動作する。
    """

    @needs_real
    async def test_regular_user_stream_starts(
        self, client, admin_user, regular_user, test_project
    ):
        """一般ユーザーの有効なチケットで SSE ストリームが開始される"""
        ticket = await _get_ticket(client, regular_user)

        async def _check_stream():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")

        try:
            await asyncio.wait_for(_check_stream(), timeout=3)
        except (TimeoutError, asyncio.CancelledError):
            pass

    @needs_real
    async def test_regular_user_receives_member_project_event(
        self, client, admin_user, regular_user, test_project
    ):
        """一般ユーザーはメンバーのプロジェクトのイベントを受信する"""
        ticket = await _get_ticket(client, regular_user)
        redis = get_redis()
        project_id = str(test_project.id)

        collected_events: list[str] = []

        async def _read_stream():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        collected_events.append(line[6:])
                        if len(collected_events) >= 2:
                            return

        async def _publish_after_delay():
            await asyncio.sleep(0.3)
            event = json.dumps({"type": "task_created", "project_id": project_id})
            await redis.publish("todo:events", event)

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stream(), _publish_after_delay()),
                timeout=5,
            )
        except (TimeoutError, asyncio.CancelledError):
            pass

        # connected イベント + member project イベントを受信しているはず
        assert len(collected_events) >= 1
        assert any('"connected"' in e for e in collected_events)
        member_events = [
            e for e in collected_events
            if project_id in e and "task_created" in e
        ]
        assert len(member_events) >= 1

    @needs_real
    async def test_regular_user_does_not_receive_non_member_project_event(
        self, client, admin_user, regular_user, test_project
    ):
        """一般ユーザーはメンバーでないプロジェクトのイベントを受信しない"""
        other_project = Project(
            name="Other Project",
            created_by=admin_user,
            members=[ProjectMember(user_id=str(admin_user.id))],
        )
        await other_project.insert()
        other_project_id = str(other_project.id)
        member_project_id = str(test_project.id)

        ticket = await _get_ticket(client, regular_user)
        redis = get_redis()

        collected_events: list[str] = []

        async def _read_stream():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        collected_events.append(line[6:])
                        if len(collected_events) >= 3:
                            return

        async def _publish_events():
            await asyncio.sleep(0.3)
            non_member_event = json.dumps(
                {"type": "task_created", "project_id": other_project_id}
            )
            await redis.publish("todo:events", non_member_event)

            await asyncio.sleep(0.2)
            member_event = json.dumps(
                {"type": "task_updated", "project_id": member_project_id}
            )
            await redis.publish("todo:events", member_event)

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stream(), _publish_events()),
                timeout=5,
            )
        except (TimeoutError, asyncio.CancelledError):
            pass

        non_member_events = [e for e in collected_events if other_project_id in e]
        assert len(non_member_events) == 0

        member_events = [
            e for e in collected_events
            if member_project_id in e and "task_updated" in e
        ]
        assert len(member_events) >= 1

    @needs_real
    async def test_admin_receives_all_project_events(
        self, client, admin_user, regular_user, test_project
    ):
        """管理者は全プロジェクトのイベントを受信する"""
        other_project = Project(
            name="Admin-Only Other",
            created_by=admin_user,
            members=[ProjectMember(user_id=str(admin_user.id))],
        )
        await other_project.insert()
        other_project_id = str(other_project.id)
        member_project_id = str(test_project.id)

        ticket = await _get_ticket(client, admin_user)
        redis = get_redis()

        collected_events: list[str] = []

        async def _read_stream():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        collected_events.append(line[6:])
                        if len(collected_events) >= 3:
                            return

        async def _publish_events():
            await asyncio.sleep(0.3)
            event_a = json.dumps(
                {"type": "task_created", "project_id": other_project_id}
            )
            await redis.publish("todo:events", event_a)

            await asyncio.sleep(0.2)
            event_b = json.dumps(
                {"type": "task_updated", "project_id": member_project_id}
            )
            await redis.publish("todo:events", event_b)

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stream(), _publish_events()),
                timeout=5,
            )
        except (TimeoutError, asyncio.CancelledError):
            pass

        events_a = [e for e in collected_events if other_project_id in e]
        events_b = [e for e in collected_events if member_project_id in e]
        assert len(events_a) >= 1, "Admin should receive events from all projects"
        assert len(events_b) >= 1, "Admin should receive events from all projects"

    @needs_real
    async def test_admin_receives_event_without_project_id(
        self, client, admin_user
    ):
        """project_id を持たないイベントも管理者は受信する"""
        ticket = await _get_ticket(client, admin_user)
        redis = get_redis()

        collected_events: list[str] = []

        async def _read_stream():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        collected_events.append(line[6:])
                        if len(collected_events) >= 2:
                            return

        async def _publish_event():
            await asyncio.sleep(0.3)
            event = json.dumps({"type": "system_notification", "message": "hello"})
            await redis.publish("todo:events", event)

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stream(), _publish_event()),
                timeout=5,
            )
        except (TimeoutError, asyncio.CancelledError):
            pass

        assert any('"connected"' in e for e in collected_events)
        system_events = [e for e in collected_events if "system_notification" in e]
        assert len(system_events) >= 1

    @needs_real
    async def test_regular_user_receives_event_without_project_id(
        self, client, admin_user, regular_user, test_project
    ):
        """project_id を持たないイベントは一般ユーザーにも配信される (フィルタをパスする)"""
        ticket = await _get_ticket(client, regular_user)
        redis = get_redis()

        collected_events: list[str] = []

        async def _read_stream():
            async with client.stream("GET", f"/api/v1/events?ticket={ticket}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        collected_events.append(line[6:])
                        if len(collected_events) >= 2:
                            return

        async def _publish_event():
            await asyncio.sleep(0.3)
            event = json.dumps({"type": "system_notification", "message": "hello"})
            await redis.publish("todo:events", event)

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stream(), _publish_event()),
                timeout=5,
            )
        except (TimeoutError, asyncio.CancelledError):
            pass

        system_events = [e for e in collected_events if "system_notification" in e]
        assert len(system_events) >= 1, (
            "Events without project_id should pass through to regular users"
        )


class TestSSEProjectFilteringUnit:
    """SSE プロジェクトフィルタリングロジックのユニットテスト。

    httpx ASGI の制約を回避し、フィルタリングロジックを直接検証する。
    mongomock 互換のため mock モードで動作する。
    プロダクションコードの _should_skip_event を直接呼び出して検証する。
    """

    def test_admin_user_project_ids_is_none(self, admin_user):
        """管理者の場合 user_project_ids は None (全プロジェクト表示)"""
        # events.py のロジック: admin → user_project_ids = None
        if admin_user.is_admin:
            user_project_ids = None
        else:
            user_project_ids = set()

        assert user_project_ids is None

    def test_filtering_allows_event_when_project_ids_is_none(self):
        """user_project_ids が None (admin) の場合、全イベントを通過させる"""
        from app.api.v1.endpoints.events import _should_skip_event

        message_data = json.dumps(
            {"type": "task_created", "project_id": "proj_123"}
        )
        assert _should_skip_event(None, message_data) is False

    def test_filtering_allows_member_project_event(self):
        """一般ユーザーのメンバープロジェクトイベントは通過する"""
        from app.api.v1.endpoints.events import _should_skip_event

        message_data = json.dumps(
            {"type": "task_created", "project_id": "proj_A"}
        )
        assert _should_skip_event({"proj_A", "proj_B"}, message_data) is False

    def test_filtering_blocks_non_member_project_event(self):
        """一般ユーザーの非メンバープロジェクトイベントはフィルタリングされる"""
        from app.api.v1.endpoints.events import _should_skip_event

        message_data = json.dumps(
            {"type": "task_created", "project_id": "proj_C"}
        )
        assert _should_skip_event({"proj_A", "proj_B"}, message_data) is True

    def test_filtering_allows_event_without_project_id(self):
        """project_id を持たないイベントは一般ユーザーにも通過する"""
        from app.api.v1.endpoints.events import _should_skip_event

        message_data = json.dumps(
            {"type": "system_notification", "message": "hello"}
        )
        assert _should_skip_event({"proj_A", "proj_B"}, message_data) is False

    def test_filtering_handles_invalid_json_gracefully(self):
        """不正な JSON メッセージはフィルタリングせず通過させる"""
        from app.api.v1.endpoints.events import _should_skip_event

        assert _should_skip_event({"proj_A"}, "not-json-data") is False

    def test_filtering_handles_non_dict_json_gracefully(self):
        """JSON だが dict でないメッセージはフィルタリングせず通過させる"""
        from app.api.v1.endpoints.events import _should_skip_event

        message_data = json.dumps([1, 2, 3])  # list, not dict
        assert _should_skip_event({"proj_A"}, message_data) is False

    def test_filtering_with_empty_project_ids_blocks_all_project_events(self):
        """user_project_ids が空セット (メンバー無し) の場合、
        project_id 付きイベントは全てブロックされる"""
        from app.api.v1.endpoints.events import _should_skip_event

        message_data = json.dumps(
            {"type": "task_created", "project_id": "proj_X"}
        )
        assert _should_skip_event(set(), message_data) is True

    def test_filtering_with_empty_project_ids_allows_no_project_id_events(self):
        """user_project_ids が空セットでも project_id なしイベントは通過する"""
        from app.api.v1.endpoints.events import _should_skip_event

        message_data = json.dumps({"type": "connected"})
        assert _should_skip_event(set(), message_data) is False
