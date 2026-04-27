"""In-process terminal session router.

Maps browser-supplied ``session_id`` to the originating browser
WebSocket(s) so the agent's ``terminal_output`` / ``terminal_exit``
envelopes can be routed back. Single-process by design — multi-worker
support would require a Redis pub/sub fan-out, deferred until needed.

Multi-WS-per-session (2026-04-27 fix):
The router stores a *set* of WebSockets per session_id, not a single
slot. React multi-mount of TerminalView (workbench layout updates
re-key the component while the previous one's effect cleanup is in
flight) opens 2-3 simultaneous WS connections for the same session.
The earlier dict-based slot kept only the latest registered WS, so
``terminal_output`` was silently lost for the WS objects backing the
visible pane whenever they happened to be one of the older
registrations. Storing a set and fanning ``dispatch`` out to every
live WS guarantees the visible pane sees the echo regardless of which
WS object happens to be the most recent register.

See ``backend/tests/unit/test_terminal_router.py`` for the production
log reproduction that pinned this behaviour.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


class TerminalSessionRouter:
    def __init__(self) -> None:
        # session_id → set of WebSockets currently registered. A set
        # (not a list) so a re-register of the same WS is idempotent.
        self._browsers: dict[str, set[WebSocket]] = {}

    def register(self, session_id: str, ws: WebSocket) -> None:
        bucket = self._browsers.setdefault(session_id, set())
        was_present = ws in bucket
        bucket.add(ws)
        logger.info(
            "terminal_router: register session=%s already_present=%s ws_count=%d total_sessions=%d",
            session_id, was_present, len(bucket), len(self._browsers),
        )

    def unregister(self, session_id: str, ws=None) -> None:
        """Remove ``ws`` from the bucket for ``session_id``.

        Identity-scoped: only the WS that calls this gets removed.
        Other still-live WS for the same session keep receiving
        dispatches. If the bucket becomes empty the session_id key is
        removed too.

        ``ws=None`` keeps the legacy "force-clear" semantics for admin
        tools that want to drop every WS for a session.
        """
        bucket = self._browsers.get(session_id)
        if bucket is None:
            return
        if ws is None:
            # Force-clear all WS for this session.
            self._browsers.pop(session_id, None)
            logger.info(
                "terminal_router: unregister force-clear session=%s removed_count=%d",
                session_id, len(bucket),
            )
            return
        if ws not in bucket:
            logger.info(
                "terminal_router: unregister noop (ws not in bucket) session=%s ws_count=%d",
                session_id, len(bucket),
            )
            return
        bucket.discard(ws)
        if not bucket:
            self._browsers.pop(session_id, None)
            logger.info(
                "terminal_router: unregister last-WS session=%s session removed",
                session_id,
            )
        else:
            logger.info(
                "terminal_router: unregister one-of-many session=%s remaining_ws=%d",
                session_id, len(bucket),
            )

    def is_registered(self, session_id: str) -> bool:
        bucket = self._browsers.get(session_id)
        return bool(bucket)

    async def dispatch(self, msg: dict) -> bool:
        """Forward an agent terminal_output/terminal_exit envelope to
        EVERY live browser WS for this session_id.

        Returns True if at least one WS received the frame, False if
        the session is unknown OR every send raised an exception.
        Per-WS send failures are logged with full traceback but do
        not stop fan-out to the remaining WS — one stale connection
        cannot starve the live ones.
        """
        payload = msg.get("payload") or {}
        session_id = payload.get("session_id")
        if not session_id:
            return False
        bucket = self._browsers.get(session_id)
        if not bucket:
            logger.warning(
                "terminal_router: no browser for session=%s (type=%s, registered_sessions=%d)",
                session_id, msg.get("type"), len(self._browsers),
            )
            return False
        # Snapshot the set so a concurrent register/unregister cannot
        # mutate the iterator. We accept that a WS that disconnects
        # mid-loop will surface as a send exception — logged below,
        # not propagated.
        targets = list(bucket)
        body = json.dumps(msg)
        bytes_n = len((msg.get("payload") or {}).get("data") or "")
        ok_count = 0
        fail_count = 0
        for ws in targets:
            try:
                await ws.send_text(body)
                ok_count += 1
            except Exception:
                fail_count += 1
                logger.exception(
                    "terminal_router: failed to dispatch %s for session=%s (will continue fan-out)",
                    msg.get("type"), session_id,
                )
        logger.info(
            "terminal_router: dispatched session=%s type=%s bytes=%d ws_ok=%d ws_fail=%d",
            session_id, msg.get("type"), bytes_n, ok_count, fail_count,
        )
        return ok_count > 0


terminal_router = TerminalSessionRouter()
