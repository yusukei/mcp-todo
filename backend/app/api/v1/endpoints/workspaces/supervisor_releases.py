"""Supervisor release distribution endpoints.

Admin REST surface (upload / list / delete) plus supervisor-token
authenticated endpoints (latest / download). Mirrors ``releases.py``
(agent releases) but with an independent storage directory and DB
collection.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from .....core.config import settings
from .....core.deps import get_admin_user_flexible
from .....models import SupervisorRelease, User
from ._shared import (
    ALLOWED_ARCHS,
    ALLOWED_CHANNELS,
    ALLOWED_OS_TYPES,
    VERSION_RE,
)
from ._supervisor_releases_util import (
    authenticate_supervisor_token,
    find_latest_release,
    release_dict,
    release_storage_path,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Admin REST endpoints ─────────────────────────────────────


@router.get("/supervisor-releases")
async def list_releases(
    os_type: str | None = Query(None),
    channel: str | None = Query(None),
    user: User = Depends(get_admin_user_flexible),
) -> list[dict]:
    """List all supervisor releases. Admin only."""
    query: dict = {}
    if os_type:
        if os_type not in ALLOWED_OS_TYPES:
            raise HTTPException(status_code=422, detail=f"Invalid os_type: {os_type}")
        query["os_type"] = os_type
    if channel:
        if channel not in ALLOWED_CHANNELS:
            raise HTTPException(status_code=422, detail=f"Invalid channel: {channel}")
        query["channel"] = channel
    releases = await SupervisorRelease.find(query).sort("-created_at").to_list()
    return [
        release_dict(r, include_download_url=True, base_url=settings.BASE_URL)
        for r in releases
    ]


@router.post("/supervisor-releases", status_code=status.HTTP_201_CREATED)
async def upload_release(
    version: str = Form(...),
    os_type: str = Form(...),
    channel: str = Form("stable"),
    arch: str = Form("x64"),
    release_notes: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(get_admin_user_flexible),
) -> dict:
    """Upload a new supervisor binary release. Admin only."""
    if not VERSION_RE.match(version):
        raise HTTPException(status_code=422, detail=f"Invalid version: {version}")
    if os_type not in ALLOWED_OS_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid os_type: {os_type}")
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(status_code=422, detail=f"Invalid channel: {channel}")
    if arch not in ALLOWED_ARCHS:
        raise HTTPException(status_code=422, detail=f"Invalid arch: {arch}")

    existing = await SupervisorRelease.find_one({
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

    base_dir = Path(settings.SUPERVISOR_RELEASES_DIR)
    base_dir.mkdir(parents=True, exist_ok=True)
    subdir = base_dir / os_type / channel / arch
    subdir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix
    if os_type == "win32" and not suffix:
        suffix = ".exe"
    target_name = f"mcp-workspace-supervisor-{version}{suffix}"
    target_path = subdir / target_name

    if target_path.exists():
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
        try:
            target_path.unlink(missing_ok=True)
        except Exception:
            logger.exception(
                "supervisor release upload: failed to clean up partial file at %s",
                target_path,
            )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write release file: {e}",
        ) from e

    if size == 0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    release = SupervisorRelease(
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
        "Supervisor release uploaded: %s/%s/%s v%s (%d bytes) by %s",
        os_type, channel, arch, version, size, user.id,
    )
    return release_dict(release, include_download_url=True, base_url=settings.BASE_URL)


@router.delete(
    "/supervisor-releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_release(
    release_id: str,
    user: User = Depends(get_admin_user_flexible),
) -> None:
    release = await SupervisorRelease.get(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    try:
        path = release_storage_path(release)
        path.unlink(missing_ok=True)
    except HTTPException:
        logger.warning(
            "Supervisor release %s had invalid storage path; deleting record only",
            release_id,
        )
    except Exception:
        logger.exception(
            "Failed to delete supervisor release file for %s", release_id,
        )
    await release.delete()


# ── Supervisor-facing endpoints (token authenticated) ────────


@router.get("/supervisor-releases/latest")
async def get_latest_release(
    os_type: str = Query(...),
    channel: str = Query("stable"),
    arch: str = Query("x64"),
    authorization: str | None = Header(None),
) -> dict:
    """Return the latest release matching the filter. Used by supervisor poll."""
    await authenticate_supervisor_token(authorization)
    if os_type not in ALLOWED_OS_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid os_type: {os_type}")
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(status_code=422, detail=f"Invalid channel: {channel}")
    release = await find_latest_release(os_type, channel, arch)
    if not release:
        raise HTTPException(status_code=404, detail="No release found")
    return release_dict(release, include_download_url=True, base_url=settings.BASE_URL)


@router.get("/supervisor-releases/{release_id}/download")
async def download_release(
    release_id: str,
    authorization: str | None = Header(None),
) -> FileResponse:
    """Stream a release binary to an authenticated supervisor."""
    await authenticate_supervisor_token(authorization)
    release = await SupervisorRelease.get(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    path = release_storage_path(release)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Release file missing on server")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={"X-Supervisor-Release-Sha256": release.sha256},
    )
