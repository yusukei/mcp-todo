"""Unit tests for the Redis Stream indexer consumer (PR 3).

These verify the dispatch logic in isolation:

- Malformed envelopes are ACKed and dropped (no silent retry loop)
- Missing Mongo document on upsert is treated as delete
- Unknown `kind` raises (treated as transient for the dispatch
  path, which means the entry stays pending — but the ``_apply``
  unit test asserts the exception directly)
- Delete path does not re-read Mongo
- The consumer group can be created twice without error (BUSYGROUP)

The end-to-end round-trip through a real Redis Stream is
exercised by the ``test_e2e_xreadgroup_loop`` integration test
using the conftest fakeredis client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.indexer_consumer import IndexerConsumer


@pytest.fixture
def consumer() -> IndexerConsumer:
    c = IndexerConsumer()
    # Inject a Mock Redis for the ACK path; the apply() tests do
    # not touch _redis at all because they call _apply() directly.
    c._redis = AsyncMock()
    return c


# ── _apply() direct tests ────────────────────────────────────


class TestApplyTaskUpsert:
    async def test_upsert_reads_mongo_and_calls_indexer(
        self, consumer: IndexerConsumer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_task = MagicMock()
        fake_task.id = "T1"

        fake_indexer = MagicMock()
        fake_indexer.upsert_task = AsyncMock()

        # Patch SearchIndexer.get_instance() and Task.get(). The
        # consumer imports these lazily inside _apply so monkeypatch
        # must reach into the modules at call time.
        import app.services.search as search_module
        import app.models as models_module
        monkeypatch.setattr(
            search_module.SearchIndexer, "get_instance",
            classmethod(lambda cls: fake_indexer),
        )
        monkeypatch.setattr(
            models_module.Task, "get",
            AsyncMock(return_value=fake_task),
        )

        await consumer._apply("task", "T1", "upsert")
        fake_indexer.upsert_task.assert_awaited_once_with(fake_task)

    async def test_upsert_missing_doc_falls_back_to_delete(
        self, consumer: IndexerConsumer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_indexer = MagicMock()
        fake_indexer.upsert_task = AsyncMock()
        fake_indexer.delete_task = AsyncMock()

        import app.services.search as search_module
        import app.models as models_module
        monkeypatch.setattr(
            search_module.SearchIndexer, "get_instance",
            classmethod(lambda cls: fake_indexer),
        )
        monkeypatch.setattr(
            models_module.Task, "get",
            AsyncMock(return_value=None),
        )

        await consumer._apply("task", "T-gone", "upsert")
        fake_indexer.delete_task.assert_awaited_once_with("T-gone")
        fake_indexer.upsert_task.assert_not_called()

    async def test_delete_does_not_read_mongo(
        self, consumer: IndexerConsumer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_indexer = MagicMock()
        fake_indexer.delete_task = AsyncMock()

        import app.services.search as search_module
        import app.models as models_module
        monkeypatch.setattr(
            search_module.SearchIndexer, "get_instance",
            classmethod(lambda cls: fake_indexer),
        )
        # Task.get must NOT be called on the delete path — make
        # it blow up if it is.
        task_get_mock = AsyncMock(side_effect=AssertionError(
            "delete path must not touch Mongo"
        ))
        monkeypatch.setattr(models_module.Task, "get", task_get_mock)

        await consumer._apply("task", "T1", "delete")
        fake_indexer.delete_task.assert_awaited_once_with("T1")
        task_get_mock.assert_not_called()


class TestApplyUnknownKind:
    async def test_unknown_kind_raises(
        self, consumer: IndexerConsumer,
    ) -> None:
        with pytest.raises(ValueError, match="Unknown index notification kind"):
            await consumer._apply("nonsense", "X1", "upsert")

    async def test_docsite_is_silently_ignored(
        self, consumer: IndexerConsumer,
    ) -> None:
        # docsite reindex is bulk-only; consumer should not raise
        # so a buggy publisher cannot stall the loop.
        await consumer._apply("docsite", "P1", "upsert")


class TestApplyIndexerNotInitialised:
    async def test_raises_if_indexer_singleton_is_none(
        self, consumer: IndexerConsumer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.services.search as search_module
        monkeypatch.setattr(
            search_module.SearchIndexer, "get_instance",
            classmethod(lambda cls: None),
        )

        with pytest.raises(RuntimeError, match="SearchIndexer not initialised"):
            await consumer._apply("task", "T1", "delete")


# ── _dispatch() tests (malformed entries get ACKed) ─────────


class TestDispatchMalformed:
    async def test_missing_kind_is_acked(
        self, consumer: IndexerConsumer,
    ) -> None:
        await consumer._dispatch("1-0", {"id": "T1", "op": "upsert"})
        consumer._redis.xack.assert_awaited_once()

    async def test_missing_id_is_acked(
        self, consumer: IndexerConsumer,
    ) -> None:
        await consumer._dispatch("1-0", {"kind": "task", "op": "upsert"})
        consumer._redis.xack.assert_awaited_once()

    async def test_bad_op_is_acked(
        self, consumer: IndexerConsumer,
    ) -> None:
        await consumer._dispatch(
            "1-0", {"kind": "task", "id": "T1", "op": "wiggle"},
        )
        consumer._redis.xack.assert_awaited_once()

    async def test_bytes_fields_are_decoded(
        self, consumer: IndexerConsumer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fakeredis / real redis may return bytes for field names and values."""
        fake_indexer = MagicMock()
        fake_indexer.delete_task = AsyncMock()

        import app.services.search as search_module
        monkeypatch.setattr(
            search_module.SearchIndexer, "get_instance",
            classmethod(lambda cls: fake_indexer),
        )

        await consumer._dispatch(
            b"1-0",
            {b"kind": b"task", b"id": b"T1", b"op": b"delete"},
        )
        fake_indexer.delete_task.assert_awaited_once_with("T1")
        consumer._redis.xack.assert_awaited_once()

    async def test_transient_failure_does_not_ack(
        self, consumer: IndexerConsumer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Tantivy write failure should leave the entry pending."""
        fake_indexer = MagicMock()
        fake_indexer.delete_task = AsyncMock(
            side_effect=RuntimeError("disk full")
        )

        import app.services.search as search_module
        monkeypatch.setattr(
            search_module.SearchIndexer, "get_instance",
            classmethod(lambda cls: fake_indexer),
        )

        await consumer._dispatch(
            "1-0", {"kind": "task", "id": "T1", "op": "delete"},
        )
        # Indexer was called...
        fake_indexer.delete_task.assert_awaited_once()
        # ...but XACK was NOT, so the entry stays pending for retry.
        consumer._redis.xack.assert_not_called()


# ── _ensure_group() idempotency ────────────────────────────


class TestEnsureGroupIdempotent:
    async def test_busygroup_error_is_swallowed(
        self, consumer: IndexerConsumer,
    ) -> None:
        consumer._redis.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )
        # Should not raise.
        await consumer._ensure_group()

    async def test_unrelated_error_propagates(
        self, consumer: IndexerConsumer,
    ) -> None:
        consumer._redis.xgroup_create = AsyncMock(
            side_effect=Exception("ERR something else")
        )
        with pytest.raises(Exception, match="something else"):
            await consumer._ensure_group()
