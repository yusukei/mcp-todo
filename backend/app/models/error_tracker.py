"""MongoDB models for the Sentry-compatible error tracker.

See `Error Tracker (Sentry互換) 機能仕様 v2` (project document
69de0492e16869d3abdfe0ca) and the v3 decision addendum
(69de08aa215337cd99d7c780) for the design that drives these shapes.

Event documents are **not** Beanie Documents: they live in daily
partition collections named ``error_events_YYYYMMDD`` and are accessed
via raw Motor in ``app.services.error_tracker.events``. Per-project
``retention_days`` is enforced by dropping old daily collections — a
single TTL index cannot express per-project retention.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum as str_enum
from typing import Any

from beanie import Document, Indexed
from pydantic import BaseModel, Field
from pymongo import IndexModel


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────

class IssueStatus(str_enum):
    unresolved = "unresolved"
    resolved = "resolved"
    ignored = "ignored"


class IssueLevel(str_enum):
    fatal = "fatal"
    error = "error"
    warning = "warning"
    info = "info"
    debug = "debug"


class AutoTaskPriority(str_enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


# ─────────────────────────────────────────────────────────────
# Embedded docs
# ─────────────────────────────────────────────────────────────

class DsnKeyRecord(BaseModel):
    """A DSN key pair. ``secret_key_hash`` is an argon2id digest.

    When a DSN is rotated, the previous record is kept with
    ``expire_at`` set so SDKs already holding the old public_key can
    drain during the grace window (default 5 minutes — §8.1).
    """

    public_key: str  # 32 hex chars
    secret_key_hash: str  # argon2id("$argon2id$...") — empty during seeding
    secret_key_prefix: str = ""  # first 8 chars of secret_key, shown in UI
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expire_at: datetime | None = None  # None = active; past = grace period over


# ─────────────────────────────────────────────────────────────
# ErrorTrackingConfig — one per mcp-todo Project that opts in.
# ─────────────────────────────────────────────────────────────

def _compute_dsn_id(project_id: str) -> int:
    """Stable numeric DSN id derived from a Mongo ObjectId hex.

    The Sentry SDK extracts the leading digit run from a DSN's
    project_id segment (``projectId.match(/^\\d+/)``). A 24-char
    Mongo ObjectId like ``69bfffad73ed736a9d13fd0f`` would be
    truncated to ``69``, breaking project lookup at ingest time.
    Encoding the project_id as a deterministic ~10-digit positive
    int keeps the DSN parseable while remaining reversible at the
    ingest endpoint via an indexed lookup on ``numeric_dsn_id``.

    crc32 is good enough — collisions among <1k configs are
    negligible and we enforce uniqueness at write time.
    """
    import zlib
    return zlib.crc32(project_id.encode("utf-8")) & 0x7FFFFFFF


class ErrorTrackingConfig(Document):
    """Per-project configuration for error ingestion."""

    project_id: Indexed(str)  # type: ignore[valid-type] — FK to Project._id as str
    # Stable numeric DSN id used in the DSN's path segment so the
    # Sentry SDK can parse it without truncating to leading digits.
    # Defaults to 0 (sentinel meaning "not yet assigned"); the save
    # hook + a startup backfill (in app.main.lifespan) populates it.
    numeric_dsn_id: Indexed(int) = 0  # type: ignore[valid-type]
    name: str = ""

    # DSN keys (primary + up to one previous during rotation grace).
    keys: list[DsnKeyRecord] = Field(default_factory=list)

    # Origin / CORS — decision #1 (Option B): wildcard opt-in with warning.
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_origin_wildcard: bool = False

    # Quotas.
    rate_limit_per_min: int = 600
    max_event_size_kb: int = 200
    retention_days: int = 30

    # PII.
    scrub_ip: bool = True

    # Auto-create task on new Issue — decision #2 (Option B).
    auto_create_task_on_new_issue: bool = True
    auto_task_priority: AutoTaskPriority = AutoTaskPriority.medium
    auto_task_assignee_id: str | None = None
    auto_task_tags: list[str] = Field(
        default_factory=lambda: ["error-tracker", "auto-generated"]
    )

    enabled: bool = True
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "error_projects"
        indexes = [
            IndexModel([("project_id", 1)], unique=True),
            # Numeric DSN id lookup at ingest time. The Sentry SDK
            # only emits numeric path segments, so the ingest URL
            # carries this value and we resolve back to the full
            # ErrorTrackingConfig from it.
            IndexModel([("numeric_dsn_id", 1)], unique=True, sparse=True),
            # Active-key lookup at ingest time. A simple non-unique
            # index on the array; the ingest path verifies
            # project_id / expiry separately.
            IndexModel([("keys.public_key", 1)]),
        ]

    def ensure_numeric_dsn_id(self) -> None:
        """Assign ``numeric_dsn_id`` if it's still the 0 sentinel.

        Call this before any ``insert()`` / ``save()`` and the
        startup backfill so existing rows pick up the field on
        first save after the upgrade.
        """
        if not self.numeric_dsn_id:
            self.numeric_dsn_id = _compute_dsn_id(self.project_id)

    async def save_updated(self) -> "ErrorTrackingConfig":
        self.ensure_numeric_dsn_id()
        self.updated_at = datetime.now(UTC)
        await self.save()
        return self

    def active_public_keys(self, now: datetime | None = None) -> list[str]:
        """Return public_keys that are still accepted for ingest."""
        now = now or datetime.now(UTC)
        out: list[str] = []
        for k in self.keys:
            if k.expire_at is None or k.expire_at > now:
                out.append(k.public_key)
        return out


# ─────────────────────────────────────────────────────────────
# ErrorIssue — aggregated by fingerprint.
# ─────────────────────────────────────────────────────────────

class ErrorIssue(Document):
    """One row per unique fingerprint per project.

    Event counts/``last_seen`` are updated from Redis-side counters on
    a short flush cadence (§4.2) to avoid WriteConflict storms.
    """

    project_id: Indexed(str)  # type: ignore[valid-type] — FK to ErrorTrackingConfig.project_id
    error_project_id: str  # FK to ErrorTrackingConfig._id (as str)

    fingerprint: str  # 32 hex chars (sha256 prefix)
    fingerprint_legacy: str | None = None  # set during symbolicate re-group

    title: str = ""  # "{exception.type}: {exception.value}" — already scrubbed
    culprit: str = ""  # "module.function" of the top in_app frame
    level: IssueLevel = IssueLevel.error
    platform: str = "javascript"

    status: IssueStatus = IssueStatus.unresolved
    resolution: str | None = None
    ignored_until: datetime | None = None

    first_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_count: int = 0
    user_count: int = 0  # HLL snapshot (§18)
    user_count_hll: bytes | None = None  # raw HyperLogLog bytes

    release: str | None = None
    environment: str | None = None

    assignee_id: str | None = None
    linked_task_ids: list[str] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "error_issues"
        indexes = [
            IndexModel(
                [("project_id", 1), ("fingerprint", 1)],
                unique=True,
                name="uniq_project_fingerprint",
            ),
            IndexModel(
                [("project_id", 1), ("status", 1), ("last_seen", -1)],
                name="list_by_project_status",
            ),
            IndexModel(
                [("project_id", 1), ("last_seen", -1)],
                name="list_by_project_recent",
            ),
        ]


# ─────────────────────────────────────────────────────────────
# ErrorRelease — Phase 1.5 (sourcemap binding target).
# ─────────────────────────────────────────────────────────────

class ErrorReleaseFile(BaseModel):
    """One uploaded sourcemap file inside a release."""

    name: str  # e.g. "~/static/js/main.abc123.js.map" (Sentry convention)
    gridfs_file_id: str  # GridFS _id as hex str
    size_bytes: int = 0
    sha256: str = ""
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ErrorRelease(Document):
    """A deployable version of the client app for symbolication."""

    project_id: Indexed(str)  # type: ignore[valid-type]
    error_project_id: str
    version: str  # e.g. "web@1.2.3" or a git sha
    files: list[ErrorReleaseFile] = Field(default_factory=list)
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "error_releases"
        indexes = [
            IndexModel(
                [("project_id", 1), ("version", 1)],
                unique=True,
                name="uniq_project_version",
            ),
        ]


# ─────────────────────────────────────────────────────────────
# ErrorAuditLog — §19.5 audit trail.
# ─────────────────────────────────────────────────────────────

class ErrorAuditLog(Document):
    """Audit trail for DSN / project-settings changes.

    Everything that mutates ``ErrorTrackingConfig`` keys or policy goes
    through this log so we can answer "who rotated the DSN and when?"
    without rummaging through application logs.
    """

    project_id: str
    error_project_id: str
    action: str  # "create_project" | "rotate_dsn" | "update_settings" | "delete_project"
    actor_id: str = ""
    actor_kind: str = ""  # "user" | "mcp" | "system"
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "error_audit_log"
        indexes = [
            IndexModel(
                [("project_id", 1), ("created_at", -1)],
                name="audit_by_project_recent",
            ),
        ]


__all__ = [
    "IssueStatus",
    "IssueLevel",
    "AutoTaskPriority",
    "DsnKeyRecord",
    "ErrorTrackingConfig",
    "ErrorIssue",
    "ErrorReleaseFile",
    "ErrorRelease",
    "ErrorAuditLog",
]
