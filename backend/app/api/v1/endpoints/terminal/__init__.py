"""Terminal remote access package — Agent WebSocket + Workspace REST API.

Architecture:
- Agents connect via WebSocket and authenticate with first message
  (see `.websocket`).
- MCP tools send commands to agents via `AgentConnectionManager`
  (request/response with Futures, lives in `app.services.agent_manager`).
- `RemoteWorkspace` links projects to agents + directories.
- Release distribution (upload / list / download / auto-push on connect)
  lives in `.releases` + `._releases_util`.

The package is split by responsibility so the file-level SRP violation of
the old 900-line ``terminal.py`` is gone:
- ``_shared``        — schemas, constants, serializers, startup cleanup
- ``_releases_util`` — version helpers, update-push payload builder
- ``agents``         — /agents CRUD + rotate-token + check-update
- ``workspaces``     — /workspaces CRUD
- ``releases``       — /releases CRUD + /latest + /download
- ``websocket``      — /agent/ws WebSocket + _RESPONSE_TYPES registry

Public re-exports:
- ``router``                    — the mounted APIRouter (prefix=/terminal)
- ``reset_all_agents_online``   — called by lifespan in ``app/main.py``
- ``settings``                  — re-exported so test fixtures can
                                  ``monkeypatch.setattr(terminal.settings, ...)``
- ``_RESPONSE_TYPES``            — accessed by ``test_terminal_response_types``
- ``_find_latest_release``,
  ``_is_newer``,
  ``_parse_version_tuple``      — accessed by ``test_agent_releases``
                                  (historical underscore-prefixed names kept
                                  as aliases to avoid breaking import paths).
"""
from __future__ import annotations

from fastapi import APIRouter

from .....core.config import settings  # re-exported for tests that patch terminal.settings
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
from .workspaces import router as _workspaces_router

router = APIRouter(prefix="/terminal", tags=["terminal"])
router.include_router(_agents_router)
router.include_router(_workspaces_router)
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
