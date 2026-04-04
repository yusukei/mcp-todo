from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from ....core.config import settings
from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Bookmark, User

router = APIRouter(prefix="/bookmark-assets", tags=["bookmark-assets"])

_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
}


@router.get("/{bookmark_id}/{filename:path}")
async def get_bookmark_asset(
    bookmark_id: str,
    filename: str,
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Serve a clipped asset (image, thumbnail) for a bookmark."""
    valid_object_id(bookmark_id)
    bm = await Bookmark.get(bookmark_id)
    if not bm or bm.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bookmark not found")

    base_dir = Path(settings.BOOKMARK_ASSETS_DIR) / bookmark_id
    file_path = (base_dir / filename).resolve()

    # Prevent path traversal
    if not str(file_path).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    content_type = _MEDIA_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
