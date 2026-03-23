import asyncio
import logging
import os
import tempfile
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ....core.config import settings
from ....core.deps import get_admin_user
from ....models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])

BACKUP_DIR = "/tmp/backups"


async def _run_subprocess(args: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """Run a subprocess asynchronously without blocking the event loop.

    Returns (returncode, stdout, stderr).
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise
    return (
        proc.returncode or 0,
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
    )


def _build_mongodump_args() -> list[str]:
    """Build mongodump command arguments from config."""
    return [
        "mongodump",
        f"--uri={settings.MONGO_URI}",
        f"--db={settings.MONGO_DBNAME}",
        "--gzip",
        "--archive",  # value will be appended by caller
    ]


def _build_mongorestore_args() -> list[str]:
    """Build mongorestore command arguments from config."""
    return [
        "mongorestore",
        f"--uri={settings.MONGO_URI}",
        f"--db={settings.MONGO_DBNAME}",
        "--gzip",
        "--archive",  # value will be appended by caller
        "--drop",
        "--nsFrom=*",
        f"--nsTo={settings.MONGO_DBNAME}.*",
    ]


@router.post("/export")
async def export_backup(_: User = Depends(get_admin_user)):
    """Create a backup using mongodump and return the .agz file."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.agz"
    filepath = os.path.join(BACKUP_DIR, filename)

    args = _build_mongodump_args()
    # Set archive output path
    args[-1] = f"--archive={filepath}"

    try:
        returncode, _, stderr = await _run_subprocess(args)
    except asyncio.TimeoutError:
        logger.error("mongodump timed out")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Backup operation timed out",
        )
    if returncode != 0:
        logger.error("mongodump failed: %s", stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="mongodump failed. Check server logs for details.",
        )

    cleanup = BackgroundTask(os.unlink, filepath)
    return FileResponse(
        filepath,
        media_type="application/gzip",
        filename=filename,
        background=cleanup,
    )


@router.post("/import")
async def import_backup(
    file: UploadFile,
    _: User = Depends(get_admin_user),
):
    """Restore a backup using mongorestore from an uploaded .agz file."""
    if not file.filename or not file.filename.endswith(".agz"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be .agz format",
        )

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(delete=False, suffix=".agz") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        args = _build_mongorestore_args()
        # Set archive input path
        args = [a for a in args if not a.startswith("--archive")]
        args.append(f"--archive={tmp_path}")

        try:
            returncode, _, stderr = await _run_subprocess(args)
        except asyncio.TimeoutError:
            logger.error("mongorestore timed out")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Restore operation timed out",
            )
        if returncode != 0:
            logger.error("mongorestore failed: %s", stderr)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="mongorestore failed. Check server logs for details.",
            )

        return {"status": "ok", "message": "Restore completed successfully"}
    finally:
        os.unlink(tmp_path)
