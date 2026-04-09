"""Redis Stream consumer for multi-worker index reindex hints.

This module is the sidecar side of the multi-worker indexing
path documented in ``docs/architecture/multi-worker-sidecar.md``
§5.1. It runs only in the ``backend-indexer`` container (the one
with ``ENABLE_INDEXERS=True``) and consumes the stream that the
API containers publish via :mod:`services.index_notifications`.

## Flow for a single notification

1. ``XREADGROUP indexer {worker_id} COUNT n BLOCK 100ms``
2. For each ``(kind, id, op)`` entry:
   - Re-read the full document from MongoDB (the source of truth)
   - Call the existing in-process ``upsert_*`` / ``delete_*``
     method on the corresponding ``*SearchIndexer`` singleton
3. ``XACK`` the entry so it does not get redelivered

## Crash recovery

Consumer groups track pending (unacked) entries per consumer. If
this process crashes mid-dispatch, the next startup's
``XAUTOCLAIM`` reclaims stale entries after a grace period and
re-processes them. Because the handlers are idempotent (each
re-reads from Mongo and calls ``upsert_*`` which deletes + adds),
replaying a notification produces the same Tantivy state.

## Dropped-notification recovery

If the stream fills up (``MAXLEN ~ 100k`` reached) or a
notification never makes it out of the API container (e.g. Redis
was down when the publisher ran), the entry is lost. This module
does NOT depend on notifications being reliable: the lost entry
is eventually reconciled by whatever out-of-band mechanism
refreshes the Tantivy index from Mongo (the existing ``rebuild``
command on each ``*SearchIndexer``).

## Failure handling

- Permanent dispatch failure (e.g. document disappeared from Mongo
  between publish and consume): log as warning and XACK the entry.
  The delete path will have been triggered by a separate
  ``notify_*_deleted`` call, so leaving the phantom in the index
  would be worse than dropping the phantom entry.
- Transient dispatch failure (Tantivy write error): log, do NOT
  XACK, let the next ``XAUTOCLAIM`` retry.
- Corrupted envelope (missing ``id`` / ``kind`` / ``op``): log,
  XACK, skip — a malformed entry can never succeed and holding
  it in the pending list would starve the consumer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from .index_notifications import CONSUMER_GROUP, STREAM_KEY

logger = logging.getLogger(__name__)


# How long to BLOCK on XREADGROUP per batch. Short so stop()
# unblocks quickly; long enough that the idle CPU cost is
# negligible.
READ_BLOCK_MS = 1000
READ_BATCH_SIZE = 64

# XAUTOCLAIM: reclaim pending entries that have been "busy" for
# longer than this many ms (another consumer crashed mid-dispatch).
# Default: 5 min — long enough that a slow legitimate index write
# is not pre-empted.
CLAIM_MIN_IDLE_MS = 5 * 60 * 1000


class IndexerConsumer:
    """Background consumer that materialises index notifications.

    Single instance per ``backend-indexer`` process. Not
    thread-safe; relies on the single-writer semantics of
    ``asyncio`` + the consumer group.
    """

    def __init__(self) -> None:
        self._consumer_name = f"indexer-{uuid.uuid4().hex[:12]}"
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._redis: Any = None  # resolved lazily in start()

    async def start(self) -> None:
        """Create the consumer group (if missing) and start the loop."""
        if self._task is not None:
            return
        from ..core.redis import get_redis
        self._redis = get_redis()
        await self._ensure_group()
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name="indexer-consumer",
        )
        logger.info(
            "IndexerConsumer started (consumer=%s, stream=%s, group=%s)",
            self._consumer_name, STREAM_KEY, CONSUMER_GROUP,
        )

    async def stop(self) -> None:
        """Gracefully stop the consumer loop."""
        if self._task is None:
            return
        self._stopping = True
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("IndexerConsumer stopped")

    async def _ensure_group(self) -> None:
        """Create the consumer group on the stream. Idempotent."""
        try:
            await self._redis.xgroup_create(
                STREAM_KEY, CONSUMER_GROUP,
                id="$", mkstream=True,
            )
            logger.info(
                "Created consumer group %s on %s",
                CONSUMER_GROUP, STREAM_KEY,
            )
        except Exception as e:
            # BUSYGROUP "Consumer Group name already exists" is
            # the expected case on every restart after the first.
            if "BUSYGROUP" in str(e):
                return
            raise

    async def _run(self) -> None:
        """Main loop: reclaim stale pending, then read new entries."""
        # Reclaim whatever the previous consumer left behind.
        await self._reclaim_stale()
        while not self._stopping:
            try:
                resp = await self._redis.xreadgroup(
                    CONSUMER_GROUP,
                    self._consumer_name,
                    {STREAM_KEY: ">"},
                    count=READ_BATCH_SIZE,
                    block=READ_BLOCK_MS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "IndexerConsumer xreadgroup failed; backing off",
                )
                await asyncio.sleep(1.0)
                continue
            if not resp:
                continue
            # resp shape: [(stream_name, [(entry_id, {fields}), ...])]
            for _stream_name, entries in resp:
                for entry_id, fields in entries:
                    await self._dispatch(entry_id, fields)

    async def _reclaim_stale(self) -> None:
        """XAUTOCLAIM any pending entries from a crashed consumer.

        Runs once at startup. If there are no stale entries this
        is a no-op. Failures here do not block start because the
        next natural XREADGROUP will still work on new entries.
        """
        try:
            start_id = "0-0"
            while True:
                result = await self._redis.xautoclaim(
                    STREAM_KEY,
                    CONSUMER_GROUP,
                    self._consumer_name,
                    min_idle_time=CLAIM_MIN_IDLE_MS,
                    start_id=start_id,
                    count=READ_BATCH_SIZE,
                )
                # redis.py returns (next_start, reclaimed, deleted)
                if not isinstance(result, (tuple, list)) or len(result) < 2:
                    return
                next_start, reclaimed = result[0], result[1]
                if not reclaimed:
                    return
                for entry_id, fields in reclaimed:
                    logger.info(
                        "Reclaimed stale entry %s from previous consumer",
                        entry_id,
                    )
                    await self._dispatch(entry_id, fields)
                if next_start in (b"0-0", "0-0", 0):
                    return
                start_id = (
                    next_start.decode("utf-8")
                    if isinstance(next_start, bytes)
                    else next_start
                )
        except Exception:
            logger.exception(
                "IndexerConsumer xautoclaim failed; continuing with new entries",
            )

    async def _dispatch(
        self, entry_id: Any, fields: dict[Any, Any],
    ) -> None:
        """Translate a single stream entry into an indexer call.

        XACKs on success and on permanent failure (malformed
        envelope, missing document); leaves the entry pending on
        transient failure so the next XAUTOCLAIM retries it.
        """
        # Normalise bytes → str for the fields dict (the redis
        # client returns bytes when decode_responses is False,
        # strings when True; tests use decode_responses=True).
        def _s(v: Any) -> str:
            return v.decode("utf-8") if isinstance(v, bytes) else str(v)

        norm = {_s(k): _s(v) for k, v in fields.items()}
        kind = norm.get("kind")
        entity_id = norm.get("id")
        op = norm.get("op")

        if not kind or not entity_id or op not in ("upsert", "delete"):
            logger.warning(
                "Dropping malformed index notification %s: %r",
                entry_id, norm,
            )
            await self._ack(entry_id)
            return

        try:
            await self._apply(kind, entity_id, op)
        except Exception:
            logger.exception(
                "Failed to apply index notification %s (kind=%s, id=%s, op=%s); "
                "leaving pending for retry",
                entry_id, kind, entity_id, op,
            )
            # Do NOT xack — the next xautoclaim will retry.
            return

        await self._ack(entry_id)

    async def _ack(self, entry_id: Any) -> None:
        try:
            await self._redis.xack(STREAM_KEY, CONSUMER_GROUP, entry_id)
        except Exception:
            logger.exception(
                "XACK failed for %s; entry will be retried on next reclaim",
                entry_id,
            )

    async def _apply(self, kind: str, entity_id: str, op: str) -> None:
        """Dispatch a single notification to the correct indexer.

        Always re-reads the document from MongoDB before upserting
        — the notification is a hint, not the data. A document
        that was deleted between publish and consume is treated
        as a delete (the delete notification will eventually
        arrive or the reconciliation sweep will remove the stale
        index entry).
        """
        if kind == "task":
            from ..models import Task
            from .search import SearchIndexer
            idx = SearchIndexer.get_instance()
            if idx is None:
                raise RuntimeError("SearchIndexer not initialised")
            if op == "delete":
                await idx.delete_task(entity_id)
                return
            task = await Task.get(entity_id)
            if task is None:
                logger.info(
                    "Task %s not found on reindex — treating as delete",
                    entity_id,
                )
                await idx.delete_task(entity_id)
                return
            await idx.upsert_task(task)

        elif kind == "knowledge":
            from ..models import Knowledge
            from .knowledge_search import KnowledgeSearchIndexer
            idx = KnowledgeSearchIndexer.get_instance()
            if idx is None:
                raise RuntimeError("KnowledgeSearchIndexer not initialised")
            if op == "delete":
                await idx.delete_knowledge(entity_id)
                return
            k = await Knowledge.get(entity_id)
            if k is None:
                logger.info(
                    "Knowledge %s not found on reindex — treating as delete",
                    entity_id,
                )
                await idx.delete_knowledge(entity_id)
                return
            await idx.upsert_knowledge(k)

        elif kind == "document":
            from ..models import ProjectDocument
            from .document_search import DocumentSearchIndexer
            idx = DocumentSearchIndexer.get_instance()
            if idx is None:
                raise RuntimeError("DocumentSearchIndexer not initialised")
            if op == "delete":
                await idx.delete_document(entity_id)
                return
            d = await ProjectDocument.get(entity_id)
            if d is None:
                logger.info(
                    "Document %s not found on reindex — treating as delete",
                    entity_id,
                )
                await idx.delete_document(entity_id)
                return
            await idx.upsert_document(d)

        elif kind == "bookmark":
            from ..models import Bookmark
            from .bookmark_search import BookmarkSearchIndexer
            idx = BookmarkSearchIndexer.get_instance()
            if idx is None:
                raise RuntimeError("BookmarkSearchIndexer not initialised")
            if op == "delete":
                await idx.delete_bookmark(entity_id)
                return
            b = await Bookmark.get(entity_id)
            if b is None:
                logger.info(
                    "Bookmark %s not found on reindex — treating as delete",
                    entity_id,
                )
                await idx.delete_bookmark(entity_id)
                return
            await idx.upsert_bookmark(b)

        elif kind == "docsite":
            # DocSite indexing happens via bulk ``import_docsite``
            # rather than per-page CRUD, so we intentionally do
            # not publish notifications for it — but if one
            # arrives (future extension), log and no-op instead
            # of raising, so a buggy publisher cannot stall the
            # consumer loop.
            logger.warning(
                "Received docsite notification for %s (op=%s) but "
                "docsite reindex is bulk-only; ignoring",
                entity_id, op,
            )
            return

        else:
            raise ValueError(f"Unknown index notification kind: {kind!r}")


# Module-level singleton. Import from anywhere; the lifespan in
# main.py calls start()/stop() during the indexer container's
# lifecycle.
indexer_consumer = IndexerConsumer()
