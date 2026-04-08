"""Shared schemas, constants, and helpers for workspaces endpoint submodules.

All cross-module state (request schemas, validation constants, agent
serializers, startup cleanup) lives here so the individual router modules
(`agents`, `releases`, `websocket`) stay focused on route handlers.

Workspace CRUD schemas / serializers lived here until 2026-04-08; they
were removed when the project → agent binding moved into the embedded
``Project.remote`` field.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from .....models.remote import RemoteAgent
from .....services.agent_manager import agent_manager

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────

# Allowed values for agent release fields
ALLOWED_OS_TYPES = {"win32", "linux", "darwin"}
ALLOWED_CHANNELS = {"stable", "beta", "canary"}
ALLOWED_ARCHS = {"x64", "arm64", "x86"}
VERSION_RE = re.compile(r"^\d+(\.\d+)*([\-+].+)?$")


# ── Schemas ──────────────────────────────────────────────────


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class AgentSettingsUpdateRequest(BaseModel):
    auto_update: bool | None = None
    update_channel: str | None = None


# ── Serializers / helpers ────────────────────────────────────


def agent_dict(a: RemoteAgent) -> dict:
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


