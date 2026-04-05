import asyncio
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ....core.config import settings
from ....core.deps import get_admin_user
from ....models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])

BACKUP_DIR = "/tmp/backups"
MAX_BACKUP_SIZE = 500 * 1024 * 1024  # 500MB

# Archive internal structure
_DB_DUMP_NAME = "db.agz"
_ASSET_DIRS = {
    "docsite_assets": "DOCSITE_ASSETS_DIR",
    "bookmark_assets": "BOOKMARK_ASSETS_DIR",
}


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


def _add_directory_to_zip(zf: zipfile.ZipFile, src_dir: str, arc_prefix: str) -> int:
    """Recursively add directory contents to a zip file. Returns file count."""
    base = Path(src_dir)
    if not base.is_dir():
        return 0
    count = 0
    for fpath in sorted(base.rglob("*")):
        if fpath.is_file():
            zf.write(fpath, f"{arc_prefix}/{fpath.relative_to(base)}")
            count += 1
    return count


def _restore_assets(work_dir: str) -> dict[str, int]:
    """Replace asset directories with contents from extracted backup."""
    restored: dict[str, int] = {}
    for arc_name, setting_attr in _ASSET_DIRS.items():
        target = Path(getattr(settings, setting_attr))

        # Clear existing (orphaned after DB restore)
        if target.exists():
            shutil.rmtree(target)

        src = Path(work_dir) / arc_name
        if src.is_dir():
            shutil.copytree(src, target)
            count = sum(1 for f in target.rglob("*") if f.is_file())
        else:
            target.mkdir(parents=True, exist_ok=True)
            count = 0
        restored[arc_name] = count
    return restored


async def _rebuild_search_indexes() -> dict[str, int | str]:
    """Rebuild all Tantivy search indexes after a database restore."""
    from ....services.search import TANTIVY_AVAILABLE

    if not TANTIVY_AVAILABLE:
        return {"status": "tantivy_not_available"}

    from ....services.bookmark_search import BookmarkSearchIndexer
    from ....services.docsite_search import DocSiteSearchIndexer
    from ....services.document_search import DocumentSearchIndexer
    from ....services.knowledge_search import KnowledgeSearchIndexer
    from ....services.search import SearchIndexer

    results: dict[str, int | str] = {}
    for name, cls in [
        ("tasks", SearchIndexer),
        ("knowledge", KnowledgeSearchIndexer),
        ("documents", DocumentSearchIndexer),
        ("docsites", DocSiteSearchIndexer),
        ("bookmarks", BookmarkSearchIndexer),
    ]:
        instance = cls.get_instance()
        if instance is None:
            results[name] = "not_initialized"
            continue
        try:
            count = await instance.rebuild()
            results[name] = count
        except Exception as e:
            logger.warning("Failed to rebuild %s search index: %s", name, e)
            results[name] = f"error: {e}"
    return results


@router.post("/export")
async def export_backup(_: User = Depends(get_admin_user)):
    """Create a full backup (MongoDB dump + asset files) as a .zip archive."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    work_dir = tempfile.mkdtemp(prefix="backup_")

    try:
        # 1. mongodump
        db_path = os.path.join(work_dir, _DB_DUMP_NAME)
        args = _build_mongodump_args()
        args[-1] = f"--archive={db_path}"

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

        # 2. Build zip: db dump + asset directories
        filename = f"backup_{timestamp}.zip"
        filepath = os.path.join(BACKUP_DIR, filename)

        with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, _DB_DUMP_NAME)
            for arc_name, setting_attr in _ASSET_DIRS.items():
                asset_dir = getattr(settings, setting_attr)
                count = _add_directory_to_zip(zf, asset_dir, arc_name)
                logger.info("Backup: %s — %d files", arc_name, count)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    cleanup = BackgroundTask(os.unlink, filepath)
    return FileResponse(
        filepath,
        media_type="application/zip",
        filename=filename,
        background=cleanup,
    )


@router.post("/import")
async def import_backup(
    file: UploadFile,
    _: User = Depends(get_admin_user),
):
    """Restore from a .zip backup (or legacy .agz)."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    is_legacy = file.filename.endswith(".agz")
    is_zip = file.filename.endswith(".zip")

    if not is_legacy and not is_zip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be .zip or .agz format",
        )

    # File size check (nginx enforces hard limit; this provides a clear message)
    if file.size is not None and file.size > MAX_BACKUP_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File too large. Maximum backup size is {MAX_BACKUP_SIZE // (1024 * 1024)}MB.",
        )

    suffix = ".agz" if is_legacy else ".zip"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        if len(content) > MAX_BACKUP_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File too large. Maximum backup size is {MAX_BACKUP_SIZE // (1024 * 1024)}MB.",
            )
        tmp.write(content)
        tmp_path = tmp.name

    work_dir = None
    try:
        if is_zip:
            work_dir = tempfile.mkdtemp(prefix="restore_")

            try:
                with zipfile.ZipFile(tmp_path, "r") as zf:
                    # Validate entries for path traversal
                    for entry in zf.namelist():
                        target = os.path.normpath(os.path.join(work_dir, entry))
                        if not target.startswith(os.path.normpath(work_dir)):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid backup: archive contains unsafe path entries",
                            )
                    zf.extractall(work_dir)
            except zipfile.BadZipFile:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid zip file",
                )

            db_path = os.path.join(work_dir, _DB_DUMP_NAME)
            if not os.path.exists(db_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid backup: {_DB_DUMP_NAME} not found in archive",
                )
        else:
            db_path = tmp_path

        # 1. Restore MongoDB
        args = _build_mongorestore_args()
        args = [a for a in args if not a.startswith("--archive")]
        args.append(f"--archive={db_path}")

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

        # 2. Restore asset files (zip format only)
        assets_restored: dict[str, int] = {}
        if is_zip and work_dir:
            assets_restored = _restore_assets(work_dir)

        # 3. Rebuild search indexes
        rebuild_results = await _rebuild_search_indexes()
        logger.info(
            "Restore completed. Assets: %s, Indexes: %s",
            assets_restored,
            rebuild_results,
        )

        return {
            "status": "ok",
            "message": "Restore completed successfully",
            "assets_restored": assets_restored,
            "indexes_rebuilt": rebuild_results,
        }
    finally:
        os.unlink(tmp_path)
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
