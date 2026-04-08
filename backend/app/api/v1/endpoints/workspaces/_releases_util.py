"""Release versioning + update-push helpers.

Kept separate from `_shared.py` because none of the Agent/Workspace routes
need these helpers — only the release REST module and the WebSocket's
auto-push path on (re)connect. Centralizing them here also keeps the
test fixtures that probe version comparison (test_agent_releases.py)
in a single predictable import location.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import HTTPException, WebSocket

from .....core.config import settings
from .....core.security import hash_api_key
from .....models import AgentRelease
from .....models.remote import RemoteAgent

logger = logging.getLogger(__name__)


def parse_version_tuple(v: str | None) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple of ints.

    Strips an optional pre-release / build suffix (anything after the first
    '-' or '+'). Non-numeric components are coerced to 0 so that malformed
    versions sort *before* well-formed ones rather than crashing the
    comparison logic. ``None`` or empty input returns ``()`` which compares
    less than every non-empty tuple, ensuring agents reporting no version
    are always considered out of date.
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


def is_newer(release_version: str, agent_version: str | None) -> bool:
    """Return True iff release_version > agent_version."""
    return parse_version_tuple(release_version) > parse_version_tuple(agent_version)


def release_dict(r: AgentRelease, *, include_download_url: bool = False, base_url: str = "") -> dict:
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
        # Use BASE_URL when configured (production), otherwise return a
        # path-only URL that the client can resolve against its own host.
        prefix = base_url.rstrip("/") if base_url else ""
        out["download_url"] = f"{prefix}/api/v1/workspaces/releases/{r.id}/download"
    return out


def release_storage_path(rel: AgentRelease) -> Path:
    """Resolve the on-disk path for a release, ensuring it stays inside AGENT_RELEASES_DIR."""
    base = Path(settings.AGENT_RELEASES_DIR).resolve()
    target = (base / rel.storage_path).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Release storage path escapes base directory") from exc
    return target


async def find_latest_release(os_type: str, channel: str, arch: str = "x64") -> AgentRelease | None:
    """Return the highest-version release matching the filter, or None.

    Beanie sort by created_at would be wrong for re-uploads, so we sort
    in Python by parsed version tuple. The result set is bounded by os_type
    and channel so this stays cheap.
    """
    releases = await AgentRelease.find(
        {"os_type": os_type, "channel": channel, "arch": arch}
    ).to_list()
    if not releases:
        return None
    releases.sort(key=lambda r: parse_version_tuple(r.version), reverse=True)
    return releases[0]


async def authenticate_agent_token(authorization: str | None) -> RemoteAgent:
    """Validate `Authorization: Bearer ta_xxx` and return the matching agent.

    Used by release-download endpoints called by remote agents.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    key_hash = hash_api_key(token)
    agent = await RemoteAgent.find_one({"key_hash": key_hash})
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return agent


def build_update_payload(release: AgentRelease) -> dict:
    """Serialize an AgentRelease into an ``update_available`` WS message.

    Extracted so ``maybe_push_update`` (auto-push on connect) and the
    admin-triggered check-update endpoint share the exact same shape.
    """
    return {
        "type": "update_available",
        "release_id": str(release.id),
        "version": release.version,
        "download_url": (
            f"{settings.BASE_URL.rstrip('/')}/api/v1/workspaces/releases/{release.id}/download"
            if settings.BASE_URL
            else f"/api/v1/workspaces/releases/{release.id}/download"
        ),
        "sha256": release.sha256,
        "size_bytes": release.size_bytes,
    }


async def maybe_push_update(ws: WebSocket, agent: RemoteAgent) -> None:
    """Check whether a newer release exists for ``agent`` and push notification.

    This runs inside the agent WebSocket auth handshake, so we cannot
    let a release-table lookup or a transient WS send failure tear the
    connection down. Both fail-cases are logged with a full traceback
    (CLAUDE.md "No error hiding" — warnings without ``exc_info`` are
    forbidden) and then swallowed at this explicit boundary. The
    handshake continues either way; the next reconnect will retry the
    update push.
    """
    if not agent.auto_update:
        return
    if not agent.os_type:
        return  # Agent hasn't reported os_type yet
    try:
        latest = await find_latest_release(agent.os_type, agent.update_channel or "stable")
    except Exception:
        logger.exception(
            "update check: failed to query releases for %s", agent.id,
        )
        return
    if latest is None:
        return
    if not is_newer(latest.version, agent.agent_version):
        return
    payload = build_update_payload(latest)
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        logger.exception(
            "update_available send failed for %s", agent.id,
        )
        return
    logger.info(
        "update_available pushed to agent=%s current=%s latest=%s",
        agent.id, agent.agent_version, latest.version,
    )
