"""Self-capture: send the backend's own unhandled exceptions to the
error tracker without going through HTTP.

Instead of posting to ``/api/{project_id}/envelope/`` (which would
be a circular HTTP call to ourselves), this module builds a minimal
Sentry-compatible event dict and writes it directly onto the
``errors:ingest`` Redis Stream — the same path the HTTP ingest
endpoint uses.

Usage
-----
Anywhere in the backend where you want to report an exception::

    from .services.error_tracker.capture import capture_exception
    await capture_exception(exc)

The function is a no-op (with a warning) when no enabled ErrorTrackingConfig
exists in the database.
"""

from __future__ import annotations

import logging
import platform
import sys
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Project-ID cache ─────────────────────────────────────────────
# We cache (project_id, error_project_id) per process to avoid a
# MongoDB round-trip on every captured exception.  A ``None`` result
# is not cached so that a project created after startup is picked up
# automatically — we just pay one extra query until it exists.

_cached_ids: tuple[str, str] | None = None  # (project_id, error_project_id)


async def _lookup_project() -> tuple[str, str] | None:
    """Return (project_id, error_project_id) for the first active
    ErrorTrackingConfig, or None if none exists yet."""
    global _cached_ids
    if _cached_ids is not None:
        return _cached_ids
    try:
        from ...models.error_tracker import ErrorTrackingConfig

        now = datetime.now(UTC)
        async for ep in ErrorTrackingConfig.find(ErrorTrackingConfig.enabled == True).sort("+created_at"):  # noqa: E712
            if ep.active_public_keys(now):
                _cached_ids = (ep.project_id, str(ep.id))
                return _cached_ids
    except Exception:
        logger.exception("error-tracker capture: failed to look up ErrorTrackingConfig")
    return None


# ── Event builder ─────────────────────────────────────────────────

def _build_sentry_event(
    exc: BaseException,
    *,
    extra: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
    event_id: str | None = None,
) -> tuple[str, bytes]:
    """Return (event_id, payload_bytes) for *exc*.

    Produces a minimal Sentry-compatible event payload that the
    worker's pipeline (``pipeline.py``) can fingerprint and persist.
    """
    from ...core.config import settings
    from .stream import json_dumps

    eid = event_id or uuid.uuid4().hex

    # Build exception chain (innermost first, outermost last —
    # Sentry renders the *last* value as the primary exception).
    values: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        tb = traceback.extract_tb(current.__traceback__)
        frames = [
            {
                "filename": f.filename,
                "lineno": f.lineno,
                "function": f.name,
                "context_line": f.line or "",
            }
            for f in tb
        ]
        values.append(
            {
                "type": type(current).__name__,
                "value": str(current),
                "module": type(current).__module__,
                "stacktrace": {"frames": frames},
            }
        )
        # Walk the cause/context chain.
        next_exc = current.__cause__ or (
            current.__context__ if not current.__suppress_context__ else None
        )
        current = next_exc  # type: ignore[assignment]

    # Sentry convention: values are innermost-first.
    values.reverse()

    event: dict[str, Any] = {
        "event_id": eid,
        "timestamp": datetime.now(UTC).isoformat(),
        "platform": "python",
        "level": "error",
        "logger": "mcp-todo.backend",
        "exception": {"values": values},
        "server_name": "mcp-todo-backend",
        "sdk": {"name": "mcp-todo.self-capture", "version": "1.0"},
        "contexts": {
            "runtime": {
                "name": "Python",
                "version": sys.version.split()[0],
            },
            "os": {
                "name": platform.system(),
                "version": platform.release(),
            },
        },
    }
    if settings.ENVIRONMENT:
        event["environment"] = settings.ENVIRONMENT
    if settings.RELEASE:
        event["release"] = settings.RELEASE
    merged_tags = {}
    if tags:
        merged_tags.update(tags)
    if merged_tags:
        event["tags"] = merged_tags
    if extra:
        event["extra"] = extra

    return eid, json_dumps(event)


# ── Public API ────────────────────────────────────────────────────

async def capture_exception(
    exc: BaseException,
    *,
    extra: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
) -> None:
    """Capture *exc* and enqueue it for the error tracker.

    Emits a WARNING and returns (does NOT raise) when:
    - No enabled ErrorTrackingConfig exists.
    - Enqueueing fails (e.g. Redis is down).

    This keeps the original error from being masked by a secondary
    failure inside the instrumentation path.
    """
    ids = await _lookup_project()
    if ids is None:
        logger.warning(
            "error-tracker capture: no ErrorTrackingConfig configured"
            " — dropping %s: %s",
            type(exc).__name__,
            exc,
        )
        return

    project_id, error_project_id = ids
    event_id, payload = _build_sentry_event(exc, extra=extra, tags=tags)

    try:
        from .stream import enqueue_event

        await enqueue_event(
            project_id=project_id,
            error_project_id=error_project_id,
            event_id=event_id,
            payload=payload,
            received_at_iso=datetime.now(UTC).isoformat(),
            item_type="event",
            client_ip=None,
            user_agent="mcp-todo-backend/self",
        )
    except Exception:
        logger.exception(
            "error-tracker capture: failed to enqueue self-captured"
            " exception %s (event_id=%s)",
            type(exc).__name__,
            event_id,
        )


__all__ = ["capture_exception"]
