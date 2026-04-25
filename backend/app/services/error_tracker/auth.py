"""DSN authentication and Origin allowlist for the error ingest API.

Implements decision #3 (authentication split) and the spec §8.1–8.2
origin reflection rule. Only concerned with *who* may post events;
PII scrubbing and rate limiting live elsewhere.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from ...models.error_tracker import ErrorTrackingConfig

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised by the ingest auth path. Carries an HTTP-ish code.

    Error codes match the spec §16.1 public slugs so the HTTP
    handler can turn them into canonical JSON responses without a
    second translation table.
    """

    def __init__(self, code: str, message: str, *, http_status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


# ── DSN header parser ─────────────────────────────────────────
#
# Sentry SDKs send a comma-separated key=value blob either in the
# ``X-Sentry-Auth`` header or (very old SDKs only) as query string
# parameters. The only mandatory field for authentication is
# ``sentry_key``; ``sentry_version``, ``sentry_client`` and
# ``sentry_timestamp`` are advisory.

_AUTH_KV_RE = re.compile(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^,]+?)\s*(?:,|$)")


def parse_sentry_auth_header(header: str | None) -> dict[str, str]:
    """Parse an ``X-Sentry-Auth`` header into a dict.

    Missing / empty / malformed input returns an empty dict so the
    caller can emit a uniform ``invalid_dsn`` response regardless
    of the shape of the failure.
    """
    if not header:
        return {}
    # The header usually starts with ``Sentry `` (the auth scheme).
    # Strip it — some SDKs omit it altogether.
    h = header.strip()
    if h.lower().startswith("sentry "):
        h = h[7:]
    out: dict[str, str] = {}
    for m in _AUTH_KV_RE.finditer(h):
        key, value = m.group(1).lower(), m.group(2).strip()
        if value:
            out[key] = value
    return out


def extract_public_key(
    *, auth_header: str | None, query: dict[str, str] | None = None
) -> str | None:
    """Return the ``sentry_key`` value from header or query string."""
    parsed = parse_sentry_auth_header(auth_header)
    if parsed.get("sentry_key"):
        return parsed["sentry_key"]
    if query:
        return query.get("sentry_key") or None
    return None


# ── Project resolution ────────────────────────────────────────


@dataclass
class AuthedProject:
    project: ErrorTrackingConfig
    public_key: str


async def resolve_error_project(
    *, url_project_id: str, public_key: str
) -> AuthedProject:
    """Authenticate the ingest request.

    The URL path carries ``project_id`` (human-visible in the DSN)
    and the ``X-Sentry-Auth`` header carries ``sentry_key``. Both
    must resolve to the same ``ErrorTrackingConfig``. Spec §8.1 decision
    #3 — this prevents a leaked DSN key for project A from being
    used against project B's URL.
    """
    if not public_key:
        raise AuthError("invalid_dsn", "missing sentry_key")

    # The DSN now embeds ``numeric_dsn_id`` (an int) in the path
    # segment because the Sentry SDK truncates non-numeric path
    # values (``projectId.match(/^\d+/)``). When the URL carries an
    # integer we resolve via the indexed ``numeric_dsn_id`` field;
    # otherwise we fall back to the legacy ObjectId lookup so old
    # SDK initialisations and direct curl tests continue to work.
    ep: ErrorTrackingConfig | None = None
    if url_project_id.isdigit():
        try:
            numeric = int(url_project_id)
        except ValueError:
            numeric = None
        if numeric is not None:
            ep = await ErrorTrackingConfig.find_one(
                ErrorTrackingConfig.numeric_dsn_id == numeric
            )
    if ep is None:
        ep = await ErrorTrackingConfig.find_one(
            ErrorTrackingConfig.project_id == url_project_id
        )
    if ep is None:
        raise AuthError(
            "project_not_found",
            f"no error project for project_id={url_project_id!r}",
            http_status=401,
        )
    if not ep.enabled:
        raise AuthError(
            "project_disabled",
            f"error project for {url_project_id!r} is disabled",
            http_status=403,
        )
    if public_key not in ep.active_public_keys(datetime.now(UTC)):
        raise AuthError(
            "invalid_dsn",
            "public_key does not match this project",
            http_status=401,
        )
    return AuthedProject(project=ep, public_key=public_key)


# ── Origin / CORS (decision #1, §8.2) ─────────────────────────


def normalize_origin(origin: str | None) -> str | None:
    """Normalise a browser ``Origin`` value to ``scheme://host[:port]``.

    Returns ``None`` for absent / blank input so the caller can
    distinguish "no Origin header" (server-to-server) from
    "bad Origin header" (browser, but unacceptable).
    """
    if not origin:
        return None
    origin = origin.strip()
    if not origin:
        return None
    try:
        u = urlparse(origin)
    except Exception:
        return None
    if not u.scheme or not u.hostname:
        return None
    host = u.hostname
    netloc = host
    if u.port is not None:
        default = (u.scheme == "http" and u.port == 80) or (
            u.scheme == "https" and u.port == 443
        )
        if not default:
            netloc = f"{host}:{u.port}"
    return f"{u.scheme}://{netloc}"


def origin_allowed(project: ErrorTrackingConfig, origin: str | None) -> bool:
    """Decide whether the given Origin may submit events.

    Spec §3.5: empty ``allowed_origins`` rejects every browser
    caller. Server-to-server callers (no Origin header) are
    allowed — rate limits still apply.
    """
    if origin is None:
        # No Origin ⇒ not a browser. Treat as server-to-server.
        return True
    normalized = normalize_origin(origin)
    if normalized is None:
        # Present but bad — refuse.
        return False
    if project.allowed_origin_wildcard:
        return True
    allowed = {normalize_origin(o) for o in project.allowed_origins}
    return normalized in allowed


def cors_headers_for(project: ErrorTrackingConfig, origin: str | None) -> dict[str, str]:
    """Return the CORS response headers.

    We **reflect** the request Origin when it is allowed, per the
    v3 §3.5 decision — no ``Allow-Origin: *`` unless wildcard opt-in
    is on. ``Vary: Origin`` is mandatory so CDN / intermediate
    caches do not serve a cached reflection to a different Origin.
    """
    headers: dict[str, str] = {
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "X-Sentry-Auth, Content-Type",
        "Access-Control-Max-Age": "600",
    }
    if origin and origin_allowed(project, origin):
        if project.allowed_origin_wildcard and origin is None:
            headers["Access-Control-Allow-Origin"] = "*"
        else:
            headers["Access-Control-Allow-Origin"] = origin
    return headers


__all__ = [
    "AuthError",
    "AuthedProject",
    "parse_sentry_auth_header",
    "extract_public_key",
    "resolve_error_project",
    "normalize_origin",
    "origin_allowed",
    "cors_headers_for",
]
