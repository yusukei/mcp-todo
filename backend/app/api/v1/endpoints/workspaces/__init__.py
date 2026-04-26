"""Workspaces endpoints package — Agent WebSocket + Agent management REST API.

Renamed from ``endpoints/terminal/`` (2026-04-08). The separate
``/api/v1/workspaces`` CRUD surface (one workspace = one project binding)
was removed on the same date: the project → agent binding moved into the
embedded ``Project.remote`` field, configured via
``PUT /api/v1/projects/{id}/remote``.

URL layout:
- ``/api/v1/workspaces/agents/...``       — remote agent CRUD + rotate-token + check-update
- ``/api/v1/workspaces/supervisors/...``  — Rust supervisor CRUD + rotate-token (Phase Y)
- ``/api/v1/workspaces/releases/...``     — agent release upload / list / download
- ``/api/v1/workspaces/supervisor-releases/...`` — supervisor release upload / list / download
- ``/api/v1/workspaces/agent/ws``         — agent WebSocket
- ``/api/v1/workspaces/supervisor/ws``    — Rust supervisor WebSocket (Phase Y)
"""
from __future__ import annotations

from fastapi import APIRouter

from .....core.config import settings  # re-exported for tests
from . import _releases_util, _shared
from ._releases_util import (
    find_latest_release as _find_latest_release,
    is_newer as _is_newer,
    parse_version_tuple as _parse_version_tuple,
)
from .agents import router as _agents_router
from .filebrowser import router as _filebrowser_router
from .releases import router as _releases_router
from .supervisor_releases import router as _supervisor_releases_router
from .supervisor_ws import router as _supervisor_ws_router
from .supervisors import router as _supervisors_router
from .terminal import router as _terminal_router
from .websocket import router as _websocket_router

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Domain sub-routers. The root ``/workspaces`` path now resolves to the
# agents router (no more standalone workspace CRUD).
router.include_router(_agents_router)
router.include_router(_supervisors_router)
router.include_router(_releases_router)
router.include_router(_supervisor_releases_router)
router.include_router(_websocket_router)
router.include_router(_supervisor_ws_router)
router.include_router(_filebrowser_router)
router.include_router(_terminal_router)

__all__ = [
    "router",
    "settings",
    "_find_latest_release",
    "_is_newer",
    "_parse_version_tuple",
]
