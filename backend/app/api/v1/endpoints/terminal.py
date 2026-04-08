"""Terminal remote access — Agent WebSocket + Workspace REST API.

Architecture:
- Agents connect via WebSocket and authenticate with first message
- MCP tools send commands to agents via AgentConnectionManager (request/response with Futures)
- RemoteWorkspace links projects to agents + directories
- All remote operations are logged to RemoteExecLog

The AgentConnectionManager itself lives in `app.services.agent_manager` so
that MCP tools and chat dispatch can import it without depending on the
FastAPI router module (which would create a circular import).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ....core.config import settings
from ....core.deps import get_admin_user
from ....core.security import hash_api_key
from ....models import AgentRelease, User
from ....models.terminal import RemoteWorkspace, TerminalAgent
from ....services.agent_manager import (
    AgentConnectionManager,
    AgentOfflineError,
    CommandTimeoutError,
    agent_manager,
)

# Re-export for backwards compatibility — external callers (chat.py, MCP tools)
# previously imported these names from this module. Keep the names available
# so existing imports continue to work, even though the canonical home is now
# `app.services.agent_manager`.
__all__ = [
    "router",
    "agent_manager",
    "AgentConnectionManager",
    "AgentOfflineError",
    "CommandTimeoutError",
    "reset_all_agents_online",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/terminal", tags=["terminal"])

# Server-side ping interval for dead connection detection
_PING_INTERVAL = 30  # seconds
_PING_TIMEOUT = 10  # seconds

# Allowed values for agent release fields
_ALLOWED_OS_TYPES = {"win32", "linux", "darwin"}
_ALLOWED_CHANNELS = {"stable", "beta", "canary"}
_ALLOWED_ARCHS = {"x64", "arm64", "x86"}
_VERSION_RE = re.compile(r"^\d+(\.\d+)*([\-+].+)?$")


async def reset_all_agents_online() -> int:
    """Reset all agents' is_online to False on server startup.

    Called from lifespan to clean up stale state from previous process.
    """
    result = await TerminalAgent.find(
        {"is_online": True}
    ).update({"$set": {"is_online": False}})
    count = result.modified_count if result else 0
    if count:
        logger.info("Reset %d stale agent online flags", count)
    return count


# ── Schemas ──────────────────────────────────────────────────


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class AgentSettingsUpdateRequest(BaseModel):
    auto_update: bool | None = None
    update_channel: str | None = None


class WorkspaceCreateRequest(BaseModel):
    agent_id: str
    project_id: str
    remote_path: str = Field(..., min_length=1, max_length=1000)
    label: str = Field("", max_length=200)


class WorkspaceUpdateRequest(BaseModel):
    remote_path: str | None = Field(None, min_length=1, max_length=1000)
    label: str | None = Field(None, max_length=200)


# ── Helpers ──────────────────────────────────────────────────


def _agent_dict(a: TerminalAgent) -> dict:
    agent_id = str(a.id)
    return {
        "id": agent_id,
        "name": a.name,
        "hostname": a.hostname,
        "os_type": a.os_type,
        "available_shells": a.available_shells,
        "is_online": agent_manager.is_connected(agent_id),
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        "created_at": a.created_at.isoformat(),
        "agent_version": a.agent_version,
        "auto_update": a.auto_update,
        "update_channel": a.update_channel,
    }


def _build_workspace_dict(
    w: RemoteWorkspace,
    agent: TerminalAgent | None,
    project,  # Project | None — typed as Any to avoid a circular import
) -> dict:
    """Build a workspace response dict from already-fetched related entities.

    Synchronous on purpose so it can be reused inside batched loops without
    triggering extra DB round-trips.
    """
    return {
        "id": str(w.id),
        "agent_id": w.agent_id,
        "agent_name": agent.name if agent else "",
        "project_id": w.project_id,
        "project_name": project.name if project else "",
        "remote_path": w.remote_path,
        "label": w.label,
        "is_online": agent_manager.is_connected(w.agent_id),
        "created_at": w.created_at.isoformat(),
        "updated_at": w.updated_at.isoformat(),
    }


def _to_object_ids(ids: list[str]) -> list[ObjectId]:
    """Convert string IDs to ObjectId, silently dropping invalid ones."""
    out: list[ObjectId] = []
    for s in ids:
        try:
            out.append(ObjectId(s))
        except (InvalidId, TypeError):
            continue
    return out


async def _workspace_dict(w: RemoteWorkspace) -> dict:
    """Single-workspace variant: fetches the related agent and project.

    Used by create/update endpoints that operate on one workspace at a time.
    For list endpoints, use the batched path inside ``list_workspaces`` to
    avoid the 2N round-trips this helper would otherwise incur.
    """
    agent = await TerminalAgent.get(w.agent_id)
    from ....models import Project
    project = await Project.get(w.project_id)
    return _build_workspace_dict(w, agent, project)


# ── Release helpers ──────────────────────────────────────────


def _parse_version_tuple(v: str | None) -> tuple[int, ...]:
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


def _is_newer(release_version: str, agent_version: str | None) -> bool:
    """Return True iff release_version > agent_version."""
    return _parse_version_tuple(release_version) > _parse_version_tuple(agent_version)


def _release_dict(r: AgentRelease, *, include_download_url: bool = False, base_url: str = "") -> dict:
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
        out["download_url"] = f"{prefix}/api/v1/terminal/releases/{r.id}/download"
    return out


def _release_storage_path(rel: AgentRelease) -> Path:
    """Resolve the on-disk path for a release, ensuring it stays inside AGENT_RELEASES_DIR."""
    base = Path(settings.AGENT_RELEASES_DIR).resolve()
    target = (base / rel.storage_path).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Release storage path escapes base directory") from exc
    return target


async def _find_latest_release(os_type: str, channel: str, arch: str = "x64") -> AgentRelease | None:
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
    releases.sort(key=lambda r: _parse_version_tuple(r.version), reverse=True)
    return releases[0]


async def _authenticate_agent_token(authorization: str | None) -> TerminalAgent:
    """Validate `Authorization: Bearer ta_xxx` and return the matching agent.

    Used by release-download endpoints called by remote agents.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    key_hash = hash_api_key(token)
    agent = await TerminalAgent.find_one({"key_hash": key_hash})
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return agent


def _build_update_payload(release: AgentRelease) -> dict:
    """Serialize an AgentRelease into an ``update_available`` WS message.

    Extracted so ``_maybe_push_update`` (auto-push on connect) and the
    admin-triggered check-update endpoint share the exact same shape.
    """
    return {
        "type": "update_available",
        "release_id": str(release.id),
        "version": release.version,
        "download_url": (
            f"{settings.BASE_URL.rstrip('/')}/api/v1/terminal/releases/{release.id}/download"
            if settings.BASE_URL
            else f"/api/v1/terminal/releases/{release.id}/download"
        ),
        "sha256": release.sha256,
        "size_bytes": release.size_bytes,
    }


async def _maybe_push_update(ws: WebSocket, agent: TerminalAgent) -> None:
    """Check whether a newer release exists for ``agent`` and push notification.

    Silently swallows lookup errors so a misconfigured release table never
    breaks the agent connect handshake.
    """
    if not agent.auto_update:
        return
    if not agent.os_type:
        return  # Agent hasn't reported os_type yet
    try:
        latest = await _find_latest_release(agent.os_type, agent.update_channel or "stable")
    except Exception as e:
        logger.warning("update check: failed to query releases for %s: %s", agent.id, e)
        return
    if latest is None:
        return
    if not _is_newer(latest.version, agent.agent_version):
        return
    payload = _build_update_payload(latest)
    try:
        await ws.send_text(json.dumps(payload))
        logger.info(
            "update_available pushed to agent=%s current=%s latest=%s",
            agent.id, agent.agent_version, latest.version,
        )
    except Exception as e:
        logger.warning("update_available send failed for %s: %s", agent.id, e)


# ── Health check ─────────────────────────────────────────────


@router.get("/health")
async def terminal_health() -> dict:
    return {"status": "ok", "websocket_endpoint": "/agent/ws"}


# ── Agent REST endpoints (admin only) ────────────────────────


@router.get("/agents")
async def list_agents(user: User = Depends(get_admin_user)) -> list[dict]:
    agents = await TerminalAgent.find(
        {"owner_id": str(user.id)}
    ).sort("-created_at").to_list()
    return [_agent_dict(a) for a in agents]


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(body: CreateAgentRequest, user: User = Depends(get_admin_user)) -> dict:
    raw_token = f"ta_{secrets.token_hex(32)}"
    agent = TerminalAgent(
        name=body.name,
        key_hash=hash_api_key(raw_token),
        owner_id=str(user.id),
    )
    await agent.insert()
    return {**_agent_dict(agent), "token": raw_token}


@router.patch("/agents/{agent_id}")
async def update_agent_settings(
    agent_id: str,
    body: AgentSettingsUpdateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    """Update auto-update flags and channel selection for an agent."""
    agent = await TerminalAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    if body.auto_update is not None:
        agent.auto_update = body.auto_update
    if body.update_channel is not None:
        if body.update_channel not in _ALLOWED_CHANNELS:
            raise HTTPException(status_code=422, detail=f"Invalid channel: {body.update_channel}")
        agent.update_channel = body.update_channel
    await agent.save()
    return _agent_dict(agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: User = Depends(get_admin_user)) -> None:
    agent = await TerminalAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    agent_manager.unregister(agent_id)  # Force unregister (no ws check)
    await agent.delete()


@router.post("/agents/{agent_id}/rotate-token")
async def rotate_agent_token(
    agent_id: str,
    user: User = Depends(get_admin_user),
) -> dict:
    """Issue a new token for an agent and invalidate the old one.

    The old key_hash is overwritten in MongoDB, so any subsequent
    auth attempt with the previous token will fail. If the agent is
    currently connected, its WebSocket is force-closed so it has to
    re-authenticate with the new token (operators must distribute the
    rotated token to the agent host before reconnecting).
    """
    agent = await TerminalAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")

    raw_token = f"ta_{secrets.token_hex(32)}"
    agent.key_hash = hash_api_key(raw_token)
    await agent.save()

    # Force-disconnect any live connection bound to the old token.
    if agent_manager.is_connected(agent_id):
        agent_manager.unregister(agent_id)
        agent.is_online = False
        try:
            await agent.save()
        except Exception:
            pass

    logger.info("Rotated token for agent %s (%s)", agent.name, agent_id)
    return {**_agent_dict(agent), "token": raw_token}


@router.post("/agents/{agent_id}/check-update")
async def check_agent_update(
    agent_id: str,
    user: User = Depends(get_admin_user),
) -> dict:
    """Manually trigger an update_available push to a connected agent.

    Useful right after uploading a new release — instead of waiting for
    the next natural WS reconnect (which fires ``_maybe_push_update``
    automatically), operators can force the check from the admin UI or
    a script. Returns ``{"pushed": false, "reason": ...}`` when no push
    was sent so callers can distinguish "not needed" from "failed".
    """
    agent = await TerminalAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent_manager.is_connected(agent_id):
        raise HTTPException(status_code=409, detail="Agent is not connected")
    if not agent.auto_update:
        return {"pushed": False, "reason": "auto_update disabled"}
    if not agent.os_type:
        return {"pushed": False, "reason": "agent os_type unknown"}
    latest = await _find_latest_release(
        agent.os_type, agent.update_channel or "stable"
    )
    if latest is None:
        return {"pushed": False, "reason": "no release available"}
    if not _is_newer(latest.version, agent.agent_version):
        return {
            "pushed": False,
            "reason": "already up to date",
            "current": agent.agent_version,
            "latest": latest.version,
        }
    payload = _build_update_payload(latest)
    try:
        await agent_manager.send_raw(agent_id, payload)
    except AgentOfflineError as exc:
        raise HTTPException(status_code=409, detail="Agent disconnected during check") from exc
    except Exception as exc:
        logger.warning("check-update: send failed for %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=f"Push failed: {exc}") from exc
    logger.info(
        "Manual update check: pushed v%s to agent=%s (current=%s)",
        latest.version, agent_id, agent.agent_version,
    )
    return {
        "pushed": True,
        "release_id": str(latest.id),
        "version": latest.version,
        "current": agent.agent_version,
    }


# ── Workspace REST endpoints (admin only) ────────────────────


@router.get("/workspaces")
async def list_workspaces(user: User = Depends(get_admin_user)) -> list[dict]:
    """List all workspaces with their agent / project details.

    Performs at most three database queries regardless of workspace count:
      1. RemoteWorkspace.find_all()
      2. TerminalAgent.find({_id: {$in: [...]}})
      3. Project.find({_id: {$in: [...]}})

    Previously this used ``[await _workspace_dict(w) ...]`` which fired
    ``2 * N`` extra round-trips (1 agent + 1 project per workspace).
    """
    workspaces = await RemoteWorkspace.find_all().sort("-created_at").to_list()
    if not workspaces:
        return []

    # Collect unique foreign-key strings, preserve dedup so the $in queries
    # don't ship duplicates to MongoDB.
    agent_id_strs = {w.agent_id for w in workspaces if w.agent_id}
    project_id_strs = {w.project_id for w in workspaces if w.project_id}

    from ....models import Project

    agent_oids = _to_object_ids(list(agent_id_strs))
    project_oids = _to_object_ids(list(project_id_strs))

    agents_task = (
        TerminalAgent.find({"_id": {"$in": agent_oids}}).to_list()
        if agent_oids
        else asyncio.sleep(0, result=[])
    )
    projects_task = (
        Project.find({"_id": {"$in": project_oids}}).to_list()
        if project_oids
        else asyncio.sleep(0, result=[])
    )
    agents, projects = await asyncio.gather(agents_task, projects_task)

    agent_by_id: dict[str, TerminalAgent] = {str(a.id): a for a in agents}
    project_by_id: dict[str, object] = {str(p.id): p for p in projects}

    return [
        _build_workspace_dict(
            w,
            agent_by_id.get(w.agent_id),
            project_by_id.get(w.project_id),
        )
        for w in workspaces
    ]


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    # Validate agent exists and belongs to user
    agent = await TerminalAgent.get(body.agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")

    # Validate project exists
    from ....models import Project
    project = await Project.get(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check uniqueness (1 project = 1 workspace)
    existing = await RemoteWorkspace.find_one({"project_id": body.project_id})
    if existing:
        raise HTTPException(status_code=409, detail="Project already has a workspace")

    workspace = RemoteWorkspace(
        agent_id=body.agent_id,
        project_id=body.project_id,
        remote_path=body.remote_path,
        label=body.label,
    )
    await workspace.insert()
    # Reuse the already-fetched agent/project to avoid two extra round-trips.
    return _build_workspace_dict(workspace, agent, project)


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    workspace = await RemoteWorkspace.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if body.remote_path is not None:
        workspace.remote_path = body.remote_path
    if body.label is not None:
        workspace.label = body.label
    workspace.updated_at = datetime.now(UTC)
    await workspace.save()
    return await _workspace_dict(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(workspace_id: str, user: User = Depends(get_admin_user)) -> None:
    workspace = await RemoteWorkspace.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await workspace.delete()


# ── Agent release REST endpoints ─────────────────────────────


@router.get("/releases")
async def list_releases(
    os_type: str | None = Query(None),
    channel: str | None = Query(None),
    user: User = Depends(get_admin_user),
) -> list[dict]:
    """List all agent releases. Admin only."""
    query: dict = {}
    if os_type:
        if os_type not in _ALLOWED_OS_TYPES:
            raise HTTPException(status_code=422, detail=f"Invalid os_type: {os_type}")
        query["os_type"] = os_type
    if channel:
        if channel not in _ALLOWED_CHANNELS:
            raise HTTPException(status_code=422, detail=f"Invalid channel: {channel}")
        query["channel"] = channel
    releases = await AgentRelease.find(query).sort("-created_at").to_list()
    return [_release_dict(r, include_download_url=True, base_url=settings.BASE_URL) for r in releases]


@router.post("/releases", status_code=status.HTTP_201_CREATED)
async def upload_release(
    version: str = Form(...),
    os_type: str = Form(...),
    channel: str = Form("stable"),
    arch: str = Form("x64"),
    release_notes: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(get_admin_user),
) -> dict:
    """Upload a new agent binary release. Admin only."""
    if not _VERSION_RE.match(version):
        raise HTTPException(status_code=422, detail=f"Invalid version: {version}")
    if os_type not in _ALLOWED_OS_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid os_type: {os_type}")
    if channel not in _ALLOWED_CHANNELS:
        raise HTTPException(status_code=422, detail=f"Invalid channel: {channel}")
    if arch not in _ALLOWED_ARCHS:
        raise HTTPException(status_code=422, detail=f"Invalid arch: {arch}")

    # Reject duplicates (same os_type + channel + arch + version)
    existing = await AgentRelease.find_one({
        "os_type": os_type,
        "channel": channel,
        "arch": arch,
        "version": version,
    })
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Release already exists for {os_type}/{channel}/{arch} v{version}",
        )

    base_dir = Path(settings.AGENT_RELEASES_DIR)
    base_dir.mkdir(parents=True, exist_ok=True)
    # Use a content-addressable subdirectory layout to avoid collisions and
    # to keep file paths predictable for ops engineers.
    subdir = base_dir / os_type / channel / arch
    subdir.mkdir(parents=True, exist_ok=True)
    # Sanitize filename — keep extension only
    suffix = Path(file.filename or "").suffix
    if os_type == "win32" and not suffix:
        suffix = ".exe"
    target_name = f"mcp-terminal-agent-{version}{suffix}"
    target_path = subdir / target_name

    if target_path.exists():
        # Should not happen given the duplicate check above, but defensive.
        raise HTTPException(status_code=409, detail="Target file already exists on disk")

    sha = hashlib.sha256()
    size = 0
    try:
        with open(target_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                sha.update(chunk)
                size += len(chunk)
                f.write(chunk)
    except Exception as e:
        # Clean up partial file on failure
        try:
            target_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to write release file: {e}") from e

    if size == 0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    release = AgentRelease(
        version=version,
        os_type=os_type,
        arch=arch,
        channel=channel,
        storage_path=str(target_path.relative_to(base_dir)).replace(os.sep, "/"),
        sha256=sha.hexdigest(),
        size_bytes=size,
        release_notes=release_notes,
        uploaded_by=str(user.id),
    )
    await release.insert()
    logger.info(
        "Agent release uploaded: %s/%s/%s v%s (%d bytes) by %s",
        os_type, channel, arch, version, size, user.id,
    )
    return _release_dict(release, include_download_url=True, base_url=settings.BASE_URL)


@router.delete("/releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_release(release_id: str, user: User = Depends(get_admin_user)) -> None:
    release = await AgentRelease.get(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    # Delete file first; ignore missing
    try:
        path = _release_storage_path(release)
        path.unlink(missing_ok=True)
    except HTTPException:
        # storage_path was malformed — still delete the DB record
        logger.warning("Release %s had invalid storage path; deleting record only", release_id)
    except Exception as e:
        logger.warning("Failed to delete release file %s: %s", release_id, e)
    await release.delete()


# ── Agent-facing release endpoints (token authenticated) ─────


@router.get("/releases/latest")
async def get_latest_release(
    os_type: str = Query(...),
    channel: str = Query("stable"),
    arch: str = Query("x64"),
    authorization: str | None = Header(None),
) -> dict:
    """Return the latest release matching the filter. Used by agents to poll."""
    await _authenticate_agent_token(authorization)
    if os_type not in _ALLOWED_OS_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid os_type: {os_type}")
    if channel not in _ALLOWED_CHANNELS:
        raise HTTPException(status_code=422, detail=f"Invalid channel: {channel}")
    release = await _find_latest_release(os_type, channel, arch)
    if not release:
        raise HTTPException(status_code=404, detail="No release found")
    return _release_dict(release, include_download_url=True, base_url=settings.BASE_URL)


@router.get("/releases/{release_id}/download")
async def download_release(
    release_id: str,
    authorization: str | None = Header(None),
) -> FileResponse:
    """Stream a release binary to an authenticated agent."""
    await _authenticate_agent_token(authorization)
    release = await AgentRelease.get(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    path = _release_storage_path(release)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Release file missing on server")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={"X-Agent-Release-Sha256": release.sha256},
    )


# ── WebSocket: Agent ─────────────────────────────────────────


_RESPONSE_TYPES = frozenset({
    "exec_result", "file_content", "write_result", "dir_listing",
    # Phase 1 handlers (added 2026-04-07): stat / mkdir / delete / move /
    # copy / glob / grep. Each handler returns one of these *_result
    # message types. If a new handler is added, its response type MUST
    # be registered here or its responses will be silently dropped and
    # the caller's Future will hang until the MCP layer's timeout.
    "stat_result", "mkdir_result", "delete_result",
    "move_result", "copy_result", "glob_result", "grep_result",
})


async def _server_ping_loop(ws: WebSocket, agent_id: str) -> None:
    """Send periodic pings to detect dead connections from the server side."""
    while True:
        await asyncio.sleep(_PING_INTERVAL)
        try:
            await asyncio.wait_for(
                ws.send_text(json.dumps({"type": "ping"})),
                timeout=_PING_TIMEOUT,
            )
        except Exception:
            logger.info("Agent %s: ping failed, connection appears dead", agent_id)
            break


@router.websocket("/agent/ws")
async def agent_websocket(ws: WebSocket):
    """Agent WebSocket with first-message authentication."""
    await ws.accept()

    # ── Auth via first message ──
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        try:
            await ws.close(code=4008, reason="Auth timeout")
        except Exception:
            pass
        return

    if msg.get("type") != "auth" or not msg.get("token"):
        try:
            await ws.close(code=4008, reason="Expected auth message")
        except Exception:
            pass
        return

    key_hash = hash_api_key(msg["token"])
    agent = await TerminalAgent.find_one({"key_hash": key_hash})
    if not agent:
        try:
            await ws.send_text(json.dumps({"type": "auth_error", "message": "Invalid token"}))
            await ws.close(code=4008, reason="Invalid agent token")
        except Exception:
            pass
        return

    agent_id = str(agent.id)
    await ws.send_text(json.dumps({"type": "auth_ok", "agent_id": agent_id}))

    # Register and close old connection if replaced
    old_ws = agent_manager.register(agent_id, ws)
    if old_ws is not None:
        try:
            await old_ws.close(code=1012, reason="Replaced by new connection")
        except Exception:
            pass

    agent.is_online = True
    agent.last_seen_at = datetime.now(UTC)
    await agent.save()
    logger.info("Agent connected: %s (%s)", agent.name, agent_id)

    # ── Server-side ping task for dead connection detection ──
    ping_task = asyncio.create_task(_server_ping_loop(ws, agent_id))

    # ── Message loop ──
    try:
        while True:
            raw = await ws.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            # Try to resolve as request/response first
            if msg_type in _RESPONSE_TYPES:
                agent_manager.resolve_request(msg)
                continue

            if msg_type == "agent_info":
                agent.hostname = msg.get("hostname", agent.hostname)
                agent.os_type = msg.get("os", agent.os_type)
                agent.available_shells = msg.get("shells", agent.available_shells)
                # New: agent reports its version on every (re)connection.
                reported_version = msg.get("agent_version")
                if reported_version:
                    agent.agent_version = reported_version
                agent.last_seen_at = datetime.now(UTC)
                await agent.save()
                # Check for available updates *after* persisting the
                # reported version so the comparison uses fresh data.
                await _maybe_push_update(ws, agent)

            elif msg_type == "pong":
                pass

            elif msg_type in ("chat_event", "chat_complete", "chat_error"):
                from .chat import handle_chat_event
                asyncio.ensure_future(handle_chat_event(msg))

    except WebSocketDisconnect:
        logger.info("Agent disconnected: %s (%s)", agent.name, agent_id)
    except Exception as e:
        logger.error("Agent WebSocket error: %s", e)
    finally:
        ping_task.cancel()
        agent_manager.unregister(agent_id, ws)  # Only remove if this is still the current connection
        agent.is_online = False
        agent.last_seen_at = datetime.now(UTC)
        try:
            await agent.save()
        except Exception:
            pass
