"""Public (unauthenticated) configuration endpoint.

Returns non-sensitive runtime configuration that the SPA needs before
it can authenticate.  Currently this includes the Sentry DSN so that
the frontend can self-configure error tracking without a build-time
environment variable.

The DSN host is derived from ``settings.BASE_URL`` (or
``settings.FRONTEND_URL`` as fallback) — never from client-supplied
``Host`` / ``X-Forwarded-*`` headers.  This prevents Host-header
injection attacks where an attacker could redirect SDK events to an
arbitrary domain.

# Zero-config mode

Operators do not need to touch the DB or the admin UI to enable
error tracking. Every Project gets an ``ErrorTrackingConfig``
auto-provisioned at creation time
(see ``services.error_tracker.provision``) with a generated DSN
key, ``allowed_origin_wildcard=True``, and a ``numeric_dsn_id``
(stable crc32-derived int that the Sentry SDK can parse without
truncating the ObjectId hex in the DSN path).

This endpoint just looks up the first enabled config and emits its
DSN. Legacy rows that predate ``numeric_dsn_id`` are migrated
on-the-fly here so the field is always populated by the time the
SDK initialises.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import ORJSONResponse

from ....core.config import settings

router = APIRouter(tags=["config"])


@router.get("/public-config")
async def public_config() -> ORJSONResponse:
    """Return public runtime config for the SPA.

    ``sentry_dsn`` is ``null`` when no active ErrorTrackingConfig exists —
    the SPA treats this as "error tracking not configured" and boots
    normally without Sentry.

    The public_key embedded in the DSN is intentionally public: it is
    designed to be shipped to browsers (write-only; gives no read
    access to stored events).
    """
    from ....models.error_tracker import ErrorTrackingConfig

    # Build the DSN host from operator-configured settings, not from
    # any client-supplied header.  BASE_URL is the canonical external
    # origin of the backend; FRONTEND_URL is used as a fallback for
    # single-origin deployments where both share the same host.
    base_url = (settings.BASE_URL or settings.FRONTEND_URL).rstrip("/")
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    now = datetime.now(UTC)
    dsn: str | None = None

    async for ep in ErrorTrackingConfig.find(ErrorTrackingConfig.enabled == True).sort("+created_at"):  # noqa: E712
        active_keys = ep.active_public_keys(now)
        if not active_keys:
            continue
        # Lazy migration: rows that predate the ``numeric_dsn_id``
        # field still exist in the DB; populate it on the first
        # /public-config hit so the Sentry SDK gets a parseable
        # path. ``provision_error_tracking_config`` sets this on
        # every freshly-created ETC, so this is purely a one-shot
        # upgrade affordance.
        if not ep.numeric_dsn_id:
            ep.ensure_numeric_dsn_id()
            await ep.save()
        public_key = active_keys[0]
        dsn = origin.replace("://", f"://{public_key}@", 1) + f"/{ep.numeric_dsn_id}"
        break

    return ORJSONResponse({"sentry_dsn": dsn})


__all__ = ["router"]
