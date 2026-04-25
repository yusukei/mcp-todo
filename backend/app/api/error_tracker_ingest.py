"""Sentry-compatible envelope ingest endpoint.

Mounted at ``/api/{project_id}/envelope/`` at root level (NOT
under ``/api/v1``) so that a DSN of the form
``https://<public_key>@<host>/api/<project_id>`` works verbatim
with the Sentry JS SDK.

The handler itself does as little work as possible (spec §2):

1. Validate DSN / Origin / payload size.
2. Parse the envelope (multi-item — §3.3).
3. Dispatch each item:
   - ``event``          — enqueue onto ``errors:ingest``.
   - ``session(s)``     — respond 200 OK, don't persist (T14 later).
   - ``transaction``    — silently dropped.
   - ``client_report``  — silently dropped.
   - ``attachment``     — 400 (MVP out-of-scope).
4. Return 200 OK with the first event_id.

Everything that follows (fingerprinting, PII scrubbing, Issue
aggregation, auto-task creation) happens asynchronously inside
the worker.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..core.config import settings
from ..services.error_tracker.auth import (
    AuthError,
    cors_headers_for,
    extract_public_key,
    origin_allowed,
    resolve_error_project,
)
from ..services.error_tracker.envelope import EnvelopeParseError, parse_envelope
from ..services.error_tracker.rate_limit import check as rate_check
from ..services.error_tracker.stream import enqueue_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["error-tracker"])


def _error_response(
    *,
    code: str,
    message: str,
    status: int,
    details: dict | None = None,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }
    headers = dict(extra_headers or {})
    return JSONResponse(status_code=status, content=body, headers=headers)


def _extract_client_ip(request: Request) -> str | None:
    """Return the originating IP per §8.2.

    ``CF-Connecting-IP`` wins when behind Cloudflare; otherwise we
    fall back to the first entry of ``X-Forwarded-For`` (nginx);
    otherwise the TCP peer. We explicitly do not blindly trust
    ``X-Forwarded-For`` alone — an adversary on the open internet
    can set it to any value.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.split(",")[0].strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


@router.options("/api/{project_id}/envelope/")
async def envelope_preflight(project_id: str, request: Request) -> Response:
    """CORS preflight for the envelope endpoint.

    Responds even when the project doesn't exist — we don't want
    to leak project existence through a 404/200 oracle. The
    browser will still fail the real POST with the canonical
    error body.
    """
    origin = request.headers.get("origin")
    from ..models.error_tracker import ErrorTrackingConfig

    # Mirror ``resolve_error_project``'s lookup order: try numeric
    # DSN id first (the Sentry SDK truncates ObjectId hex to its
    # leading digit run), fall back to the legacy ObjectId path.
    project: ErrorTrackingConfig | None = None
    if project_id.isdigit():
        try:
            numeric = int(project_id)
            project = await ErrorTrackingConfig.find_one(
                ErrorTrackingConfig.numeric_dsn_id == numeric
            )
        except ValueError:
            project = None
    if project is None:
        project = await ErrorTrackingConfig.find_one(
            ErrorTrackingConfig.project_id == project_id
        )
    if project is None or not origin_allowed(project, origin):
        # Respond 204 but with no ``Allow-Origin``; the preflight
        # then fails safely inside the browser.
        return Response(status_code=204, headers={"Vary": "Origin"})
    return Response(status_code=204, headers=cors_headers_for(project, origin))


@router.post("/api/{project_id}/envelope/")
async def envelope_ingest(project_id: str, request: Request) -> Response:
    origin = request.headers.get("origin")

    # 1) Size gate BEFORE reading body in memory.
    content_length = request.headers.get("content-length")
    max_bytes = settings.ERROR_TRACKER_MAX_ENVELOPE_KB * 1024
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                return _error_response(
                    code="payload_too_large",
                    message=f"envelope exceeds {max_bytes} bytes",
                    status=413,
                )
        except ValueError:
            pass  # malformed header — let the body read fail cleanly below

    body = await request.body()
    if len(body) > max_bytes:
        return _error_response(
            code="payload_too_large",
            message=f"envelope exceeds {max_bytes} bytes",
            status=413,
        )

    # 2) DSN auth.
    try:
        public_key = extract_public_key(
            auth_header=request.headers.get("x-sentry-auth"),
            query=dict(request.query_params),
        )
        if not public_key:
            raise AuthError("invalid_dsn", "missing sentry_key in header/query")
        authed = await resolve_error_project(
            url_project_id=project_id, public_key=public_key,
        )
    except AuthError as e:
        logger.info(
            "error-tracker: auth rejected for project=%s: %s",
            project_id,
            e.code,
        )
        return _error_response(
            code=e.code, message=str(e), status=e.http_status,
        )

    project = authed.project

    # 3) Origin allowlist (only for browser callers with Origin hdr).
    if not origin_allowed(project, origin):
        return _error_response(
            code="origin_not_allowed",
            message="Origin is not in the project allowlist",
            status=403,
            extra_headers={"Vary": "Origin"},
        )

    # 4) Rate limit (T7 / §9). Fail-closed on Redis outage (503).
    decision = await rate_check(project)
    if not decision.allowed:
        headers = cors_headers_for(project, origin)
        headers["Retry-After"] = str(decision.retry_after_sec)
        headers["X-Sentry-Rate-Limits"] = (
            f"{decision.retry_after_sec}::organization"
        )
        status = 429 if decision.limit > 0 else 503
        return _error_response(
            code="rate_limited" if status == 429 else "internal",
            message="rate limit exceeded"
            if status == 429
            else "rate limiter unavailable",
            status=status,
            details={"limit_per_min": decision.limit},
            extra_headers=headers,
        )

    # 5) Parse envelope (multi-item).
    try:
        env = parse_envelope(body)
    except EnvelopeParseError as e:
        return _error_response(
            code="invalid_envelope",
            message=str(e),
            status=400,
            extra_headers=cors_headers_for(project, origin),
        )

    received_iso = datetime.now(UTC).isoformat()
    client_ip = _extract_client_ip(request)
    user_agent = request.headers.get("user-agent")
    first_event_id: str | None = env.event_id
    enqueued = 0
    saw_attachment = False

    for item in env.items:
        t = (item.type or "").lower()
        if t == "event":
            # Prefer the item's ``event_id`` header when present
            # (Sentry spec — item header may override envelope
            # header). Fall back to envelope-level id.
            eid = (
                str(item.header.get("event_id") or "")
                or env.event_id
                or _random_event_id()
            )
            if first_event_id is None:
                first_event_id = eid
            await enqueue_event(
                project_id=project.project_id,
                error_project_id=str(project.id),
                event_id=eid,
                payload=item.payload,
                received_at_iso=received_iso,
                item_type="event",
                client_ip=client_ip,
                user_agent=user_agent,
            )
            enqueued += 1
        elif t in ("session", "sessions"):
            # Explicit 200-style acceptance (spec §3.3). Nothing
            # to store in MVP, but we DO NOT 4xx or it triggers a
            # retry storm inside the SDK.
            continue
        elif t == "transaction":
            logger.debug("error-tracker: dropped transaction item")
            continue
        elif t == "client_report":
            logger.debug("error-tracker: dropped client_report item")
            continue
        elif t == "attachment":
            saw_attachment = True
            continue
        else:
            logger.debug("error-tracker: dropped unknown item type=%s", t)

    if saw_attachment and enqueued == 0:
        # Attachment-only envelopes are refused while MVP is
        # event-only — otherwise SDKs that uploaded attachments
        # with no event would think they went through.
        return _error_response(
            code="unsupported_item",
            message="attachment items are not supported",
            status=400,
            extra_headers=cors_headers_for(project, origin),
        )

    response_body = {"id": first_event_id} if first_event_id else {}
    headers = cors_headers_for(project, origin)
    return JSONResponse(response_body, status_code=200, headers=headers)


def _random_event_id() -> str:
    """Generate a Sentry-shaped event_id (hex, 32 chars, no dashes)."""
    import uuid as _uuid

    return _uuid.uuid4().hex


__all__ = ["router"]
