from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from ....core.config import settings
from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Project, Task, User

router = APIRouter(prefix="/attachments", tags=["attachments"])

UPLOADS_DIR = Path(settings.UPLOADS_DIR)


@router.get("/{task_id}/{filename}")
async def serve_attachment(
    task_id: str, filename: str, user: User = Depends(get_current_user)
) -> FileResponse:
    valid_object_id(task_id)

    # Verify the filename exists in the task's attachments list
    task = await Task.get(task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    # Project-level access control
    from ....models.project import ProjectStatus as _ProjectStatus

    project = await Project.get(task.project_id)
    if not project or project.status == _ProjectStatus.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")

    if not any(a.filename == filename for a in task.attachments):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    file_path = UPLOADS_DIR / task_id / filename

    # Prevent path traversal (before file existence check)
    try:
        file_path.resolve().relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    return FileResponse(file_path)
