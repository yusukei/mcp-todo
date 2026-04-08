"""Workspaces endpoints package — Agent WebSocket + Workspace REST API.

Renamed from ``endpoints/terminal/`` (Phase 1+2 of the terminal → workspaces
rename, 2026-04-08). Phase 5 dropped the back-compat ``legacy_terminal_router``
once all agents had self-updated to v0.3.0 and switched to the new
``/api/v1/workspaces/agent/ws`` URL.

URL layout:
- ``GET / POST /api/v1/workspaces``                 — workspace list / create
- ``PATCH / DELETE /api/v1/workspaces/{id}``        — workspace update / delete
- ``/api/v1/workspaces/agents/...``                 — remote agent CRUD + rotate-token + check-update
- ``/api/v1/workspaces/releases/...``               — agent release upload / list / download
- ``/api/v1/workspaces/agent/ws``                   — agent WebSocket
"""
from __future__ import annotations

from fastapi import APIRouter, status

from .....core.config import settings  # re-exported for tests
from . import _releases_util, _shared
from ._releases_util import (
    find_latest_release as _find_latest_release,
    is_newer as _is_newer,
    parse_version_tuple as _parse_version_tuple,
)
from ._shared import reset_all_agents_online
from .agents import router as _agents_router
from .releases import router as _releases_router
from .websocket import _RESPONSE_TYPES, router as _websocket_router
from .workspaces import (
    create_workspace,
    list_workspaces,
    router as _workspace_crud_router,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Workspace CRUD root paths attach directly because FastAPI's
# include_router refuses both prefix="" and route_path="" at the same time.
router.add_api_route("", list_workspaces, methods=["GET"])
router.add_api_route(
    "",
    create_workspace,
    methods=["POST"],
    status_code=status.HTTP_201_CREATED,
)

# Per-id workspace routes (PATCH/DELETE /{workspace_id}) live in the
# workspaces.py sub-router.
router.include_router(_workspace_crud_router)

# Other domain sub-routers
router.include_router(_agents_router)
router.include_router(_releases_router)
router.include_router(_websocket_router)

__all__ = [
    "router",
    "reset_all_agents_online",
    "settings",
    "_RESPONSE_TYPES",
    "_find_latest_release",
    "_is_newer",
    "_parse_version_tuple",
]
