"""Supervisor release helpers — version comparison, storage path
resolution, and update push to a connected supervisor.

Mirrors ``_releases_util.py`` (which is agent-scoped). Kept separate
because the supervisor / agent release lifecycles run on independent
cadences and the WS push paths target different transports.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import HTTPException, WebSocket

from .....core.config import settings
from .....core.security import hash_api_key
from .....models import SupervisorRelease
from .....models.remote import RemoteSupervisor

logger = logging.getLogger(__name__)


def parse_version_tuple(v: str | None) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple of ints.

    Identical semantics to the agent-side version parser; duplicated
    intentionally so the agent and supervisor release modules can be
    refactored independently if their version schemes diverge.
    """
    if not v:
        return ()
    head = re.split(r"[\-+]", v, maxsplit=1)[0]
    parts: list[int] = []
    for piece in head.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(release_version: str, current_version: str | None) -> bool:
    """Return True iff release_version > current_version."""
    return parse_version_tuple(release_version) > parse_version_tuple(current_version)


def release_dict(
    r: SupervisorRelease,
    *,
    include_download_url: bool = False,
    base_url: str = "",
) -> dict:
    out = {
        "id": str(r.id),
        "version": r.version,
        "os_type": r.os_type,
        "arch": r.arch,
        "channel": r.channel,
        "sha256": r.sha256,
        "size_bytes": r.size_bytes,
        "release_notes": r.release_notes,
        "uploaded_by": r.uploaded_by,
        "created_at": r.created_at.isoformat(),
    }
    if include_download_url:
        prefix = base_url.rstrip("/") if base_url else ""
        out["download_url"] = (
            f"{prefix}/api/v1/workspaces/supervisor-releases/{r.id}/download"
        )
    return out


def release_storage_path(rel: SupervisorRelease) -> Path:
    """Resolve the on-disk path for a supervisor release, ensuring it
    stays inside SUPERVISOR_RELEASES_DIR."""
    base = Path(settings.SUPERVISOR_RELEASES_DIR).resolve()
    target = (base / rel.storage_path).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="Release storage path escapes base directory",
        ) from exc
    return target


async def find_latest_release(
    os_type: str, channel: str, arch: str = "x64",
) -> SupervisorRelease | None:
    """Return the highest-version release matching the filter, or None."""
    releases = await SupervisorRelease.find(
        {"os_type": os_type, "channel": channel, "arch": arch}
    ).to_list()
    if not releases:
        return None
    releases.sort(key=lambda r: parse_version_tuple(r.version), reverse=True)
    return releases[0]


async def authenticate_supervisor_token(authorization: str | None) -> RemoteSupervisor:
    """Validate ``Authorization: Bearer sv_xxx`` and return the supervisor.

    Used by release-download endpoints called by the Rust supervisor.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    key_hash = hash_api_key(token)
    supervisor = await RemoteSupervisor.find_one({"key_hash": key_hash})
    if not supervisor:
        raise HTTPException(status_code=401, detail="Invalid supervisor token")
    return supervisor


def build_update_payload(release: SupervisorRelease) -> dict:
    """Serialize a SupervisorRelease into a ``supervisor_upgrade`` WS message.

    Shape matches the Rust supervisor's ``handlers::supervisor_upgrade``
    expectation (Rust Supervisor design v2 §3.1):
    ``{type: "supervisor_upgrade", release_id, version, download_url, sha256, size_bytes}``.
    """
    return {
        "type": "supervisor_upgrade",
        "release_id": str(release.id),
        "version": release.version,
        "download_url": (
            f"{settings.BASE_URL.rstrip('/')}/api/v1/workspaces/supervisor-releases/{release.id}/download"
            if settings.BASE_URL
            else f"/api/v1/workspaces/supervisor-releases/{release.id}/download"
        ),
        "sha256": release.sha256,
        "size_bytes": release.size_bytes,
    }


async def maybe_push_update(ws: WebSocket, supervisor: RemoteSupervisor) -> None:
    """Push a ``supervisor_upgrade`` notification when a newer release exists.

    Mirrors the agent-side push path. Errors are logged with full
    tracebacks (CLAUDE.md "No error hiding") and swallowed at this
    handshake boundary so a transient lookup or send failure does not
    tear down the supervisor WS.
    """
    if not supervisor.os_type:
        return
    try:
        latest = await find_latest_release(supervisor.os_type, "stable")
    except Exception:
        logger.exception(
            "supervisor update check: failed to query releases for %s",
            supervisor.id,
        )
        return
    if latest is None:
        return
    if not is_newer(latest.version, supervisor.supervisor_version):
        return
    payload = build_update_payload(latest)
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        logger.exception(
            "supervisor_upgrade send failed for %s",
            supervisor.id,
        )
        return
    logger.info(
        "supervisor_upgrade pushed to supervisor=%s current=%s latest=%s",
        supervisor.id, supervisor.supervisor_version, latest.version,
    )
