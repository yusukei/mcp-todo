"""Unit tests for the index notification publisher.

Covers the no-op path (``ENABLE_INDEXERS=True`` skips the XADD),
the publish path (``ENABLE_INDEXERS=False`` goes through to
Redis), the XADD envelope shape, and the input validation that
refuses to publish an empty entity id.

The tests inject a capturing fake in place of the global Redis
client so the publisher can be exercised without pub/sub
delivery. The consumer-side round-trip (reading the stream back
and dispatching into Tantivy) is covered by PR 3's
``test_indexer_consumer``.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.core.redis as redis_module
from app.services import index_notifications


class _CapturingRedis:
    """Minimal Redis double that records XADD calls."""

    def __init__(self) -> None:
        self.xadd_calls: list[dict[str, Any]] = []

    async def xadd(
        self,
        stream: str,
        fields: dict[str, Any],
        *,
        maxlen: int | None = None,
        approximate: bool = False,
    ) -> str:
        self.xadd_calls.append({
            "stream": stream,
            "fields": fields,
            "maxlen": maxlen,
            "approximate": approximate,
        })
        return "0-0"


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _CapturingRedis:
    """Swap in a capturing Redis double for the duration of the test."""
    fake = _CapturingRedis()
    monkeypatch.setattr(redis_module, "_client", fake)
    return fake


class TestNoOpWhenIndexersEnabled:
    """In-process deployments skip the publish (no redundant XADD)."""

    async def test_upsert_is_noop_when_indexers_enabled(
        self, fake_redis: _CapturingRedis, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "ENABLE_INDEXERS", True)
        await index_notifications.notify_task_upserted("T1")
        assert fake_redis.xadd_calls == []

    async def test_delete_is_noop_when_indexers_enabled(
        self, fake_redis: _CapturingRedis, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "ENABLE_INDEXERS", True)
        await index_notifications.notify_task_deleted("T1")
        assert fake_redis.xadd_calls == []


class TestPublishWhenIndexersDisabled:
    """Multi-worker API containers actually publish the hint."""

    @pytest.fixture(autouse=True)
    def _disable_indexers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import settings
        monkeypatch.setattr(settings, "ENABLE_INDEXERS", False)

    async def test_task_upsert_envelope(self, fake_redis: _CapturingRedis) -> None:
        await index_notifications.notify_task_upserted("T123")
        assert len(fake_redis.xadd_calls) == 1
        call = fake_redis.xadd_calls[0]
        assert call["stream"] == index_notifications.STREAM_KEY
        assert call["fields"] == {"kind": "task", "id": "T123", "op": "upsert"}
        assert call["maxlen"] == index_notifications.STREAM_MAX_LEN
        assert call["approximate"] is True

    async def test_task_delete_envelope(self, fake_redis: _CapturingRedis) -> None:
        await index_notifications.notify_task_deleted("T123")
        call = fake_redis.xadd_calls[0]
        assert call["fields"] == {"kind": "task", "id": "T123", "op": "delete"}

    @pytest.mark.parametrize(
        "notifier,expected_kind",
        [
            (index_notifications.notify_knowledge_upserted, "knowledge"),
            (index_notifications.notify_document_upserted, "document"),
            (index_notifications.notify_docsite_upserted, "docsite"),
            (index_notifications.notify_bookmark_upserted, "bookmark"),
        ],
    )
    async def test_every_entity_kind_has_a_publisher(
        self,
        fake_redis: _CapturingRedis,
        notifier,
        expected_kind: str,
    ) -> None:
        await notifier("X999")
        call = fake_redis.xadd_calls[0]
        assert call["fields"]["kind"] == expected_kind
        assert call["fields"]["id"] == "X999"
        assert call["fields"]["op"] == "upsert"


class TestInputValidation:
    """Empty entity ids are a bug at the call site — fail loudly."""

    async def test_empty_id_raises(
        self, fake_redis: _CapturingRedis, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings
        monkeypatch.setattr(settings, "ENABLE_INDEXERS", False)

        with pytest.raises(ValueError, match="empty entity_id"):
            await index_notifications.notify_task_upserted("")
        # Nothing was published either.
        assert fake_redis.xadd_calls == []
