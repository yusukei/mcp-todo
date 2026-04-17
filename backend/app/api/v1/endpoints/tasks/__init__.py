"""Task endpoints package.

Splits the former 644-line ``tasks.py`` into focused submodules:
- ``_shared``     — schemas, constants, access helpers
- ``crud``        — list / create / read / update / delete
- ``lifecycle``   — complete / reopen / archive / unarchive
- ``bulk``        — reorder / export / batch update
- ``comments``    — comment add / delete
- ``attachments`` — attachment upload / delete

``router`` is the aggregated APIRouter mounted at
``/projects/{project_id}/tasks``. The attachment *serving* endpoint
(``GET /attachments/...``) lives in ``endpoints/attachments.py`` — it
uses a different URL prefix and is not part of this package.

``list_tasks`` and ``create_task`` are attached directly to this
aggregating router rather than included via ``include_router`` because
FastAPI forbids ``include_router`` with both an empty sub-router prefix
and an empty route path.
"""
from __future__ import annotations

from fastapi import APIRouter, status

from .attachments import router as _attachments_router
from .bulk import router as _bulk_router
from .comments import router as _comments_router
from .crud import create_task, list_tasks, router as _crud_router
from .lifecycle import router as _lifecycle_router
from .links import router as _links_router

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])

# Register fixed-path bulk routes FIRST so they aren't shadowed by the
# dynamic /{task_id} routes from crud.
router.include_router(_bulk_router)

# Root-path routes (GET ""/POST "") attach directly to the aggregating router.
router.add_api_route("", list_tasks, methods=["GET"])
router.add_api_route(
    "",
    create_task,
    methods=["POST"],
    status_code=status.HTTP_201_CREATED,
)

router.include_router(_lifecycle_router)
router.include_router(_comments_router)
router.include_router(_attachments_router)
# Register link routes BEFORE the dynamic /{task_id} routes in crud so the
# fixed path segment ``/links`` takes precedence over the catch-all.
router.include_router(_links_router)
router.include_router(_crud_router)

__all__ = ["router"]
