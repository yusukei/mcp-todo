"""Redis Stream publisher for cross-container index reindex hints.

This module is the API-side half of the multi-worker sidecar
indexing path documented in
``docs/architecture/multi-worker-sidecar.md`` §5.1. API workers
call the ``notify_*`` helpers after every MongoDB write that
affects searchable text; the helpers ``XADD`` a ``(kind, id, op)``
tuple onto ``INDEXER_STREAM_KEY``. The dedicated ``backend-indexer``
container consumes the stream via a consumer group and re-reads
the document from MongoDB before updating its Tantivy index
(see :mod:`services.indexer_consumer`).

## When the publish is a no-op

If the **same process** already owns the Tantivy writers
(``settings.ENABLE_INDEXERS = True``), the existing in-process
indexer has already been called synchronously by the API code
path, so a notification would only cause a redundant second
reindex. In that configuration the publish is a no-op — the
single-process deployment keeps running exactly as before and
the PR carries zero behavioural change.

## Why the notification is a hint, not the data

The envelope deliberately carries only the Mongo ``_id``. The
consumer re-reads the full document from Mongo before indexing,
which means:

- A mis-serialised notification cannot poison the index.
- Replaying the same notification is idempotent.
- A crashed / restarted indexer's catch-up sweep can recover
  from Mongo directly without needing the stream to be intact.

## Failure handling

Per CLAUDE.md "no silent fallbacks", a failed ``XADD`` is a loud
error — the operator sees it in the logs and the request that
triggered it surfaces a 5xx. We do NOT swallow the exception and
rely on a catch-up sweep to eventually reindex the document,
because doing so would hide a persistent Redis outage and give
the caller a misleading 200 response.

## Stream trim policy

``MAXLEN ~ STREAM_MAX_LEN`` caps the stream at ~100k entries
(approximate trim is cheaper than exact). If the indexer is down
long enough for the stream to hit the cap, the oldest
notifications are dropped; the periodic catch-up loop on the
indexer side will rediscover those documents by scanning Mongo.
"""

from __future__ import annotations

import logging
from typing import Literal

from ..core.config import settings
from ..core.redis import get_redis

logger = logging.getLogger(__name__)


# Redis Stream key and consumer group. Kept module-level so the
# consumer (in PR 3) can import the same constants and stay in
# sync with the publisher.
STREAM_KEY = "index:tasks"
CONSUMER_GROUP = "indexer"

# Approximate trim cap — prevents a stuck indexer from filling
# Redis memory with index notifications. ``XADD MAXLEN ~`` uses
# the approximate trim strategy which trims in O(1) amortised
# cost vs the exact ``MAXLEN =`` which scans the stream.
STREAM_MAX_LEN = 100_000


# The five searchable entity kinds. Matches the
# ``_SEARCH_INDEX_REGISTRY`` in ``main.py`` one-to-one. Using a
# Literal type lets the Mypy reader catch typos at the call site.
IndexKind = Literal["task", "knowledge", "document", "docsite", "bookmark"]

# Operation types. ``upsert`` covers both create and update (the
# indexer reads the fresh Mongo state either way). ``delete`` is
# a tombstone — the indexer removes the entry from the Tantivy
# index and does NOT re-read Mongo (because the document is gone).
IndexOp = Literal["upsert", "delete"]


async def _publish(kind: IndexKind, entity_id: str, op: IndexOp) -> None:
    """Publish a single reindex notification to the shared stream.

    Called by the public ``notify_*`` helpers below. Skips the
    XADD when indexers run in the same process (same-process
    deployments already call the indexer synchronously).
    """
    if settings.ENABLE_INDEXERS:
        # Same-process deployment. The CRUD handler already called
        # the in-process indexer synchronously, so a notification
        # would be a redundant round-trip.
        return
    if not entity_id:
        # An empty id is a bug at the call site — fail loudly
        # instead of publishing garbage.
        raise ValueError(
            f"index_notifications._publish: refusing to publish "
            f"empty entity_id for kind={kind!r} op={op!r}"
        )
    redis = get_redis()
    await redis.xadd(
        STREAM_KEY,
        {"kind": kind, "id": entity_id, "op": op},
        maxlen=STREAM_MAX_LEN,
        approximate=True,
    )
    logger.debug(
        "index notification published: kind=%s id=%s op=%s",
        kind, entity_id, op,
    )


# ── Public helpers (one pair per entity kind) ───────────────────
#
# Call sites use these by name rather than ``_publish("task", ...)``
# so grep finds every producer when a new entity kind is added
# and so typos cannot reach Redis.


async def notify_task_upserted(task_id: str) -> None:
    await _publish("task", task_id, "upsert")


async def notify_task_deleted(task_id: str) -> None:
    await _publish("task", task_id, "delete")


async def notify_knowledge_upserted(entry_id: str) -> None:
    await _publish("knowledge", entry_id, "upsert")


async def notify_knowledge_deleted(entry_id: str) -> None:
    await _publish("knowledge", entry_id, "delete")


async def notify_document_upserted(document_id: str) -> None:
    await _publish("document", document_id, "upsert")


async def notify_document_deleted(document_id: str) -> None:
    await _publish("document", document_id, "delete")


async def notify_docsite_upserted(page_id: str) -> None:
    await _publish("docsite", page_id, "upsert")


async def notify_docsite_deleted(page_id: str) -> None:
    await _publish("docsite", page_id, "delete")


async def notify_bookmark_upserted(bookmark_id: str) -> None:
    await _publish("bookmark", bookmark_id, "upsert")


async def notify_bookmark_deleted(bookmark_id: str) -> None:
    await _publish("bookmark", bookmark_id, "delete")
