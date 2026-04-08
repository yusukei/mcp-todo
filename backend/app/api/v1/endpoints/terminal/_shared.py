"""Shared schemas, constants, and helpers for terminal endpoint submodules.

All cross-module state (request schemas, validation constants, workspace and
agent serializers, startup cleanup) lives here so the individual router
modules (`agents`, `workspaces`, `releases`, `websocket`) stay focused on
route handlers.
"""
from __future__ import annotations

import asyncio
import logging
import re

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel, Field

from .....models.terminal import RemoteWorkspace, TerminalAgent
from .....services.agent_manager import agent_manager

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────

# Server-side ping interval for dead connection detection
PING_INTERVAL = 30  # seconds
PING_TIMEOUT = 10  # seconds

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


class WorkspaceCreateRequest(BaseModel):
    agent_id: str
    project_id: str
    remote_path: str = Field(..., min_length=1, max_length=1000)
    label: str = Field("", max_length=200)


class WorkspaceUpdateRequest(BaseModel):
    remote_path: str | None = Field(None, min_length=1, max_length=1000)
    label: str | None = Field(None, max_length=200)


# ── Startup cleanup ──────────────────────────────────────────


async def reset_all_agents_online() -> int:
    """Reset all agents' is_online to False on server startup.

    Called from lifespan to clean up stale state from previous process.
    """
    result = await TerminalAgent.find(
        {"is_online": True}
    ).update({"$set": {"is_online": False}})
    count = result.modified_count if result else 0
    if count:
        logger.info("Reset %d stale agent online flags", count)
    return count


# ── Serializers / helpers ────────────────────────────────────


def agent_dict(a: TerminalAgent) -> dict:
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


def build_workspace_dict(
    w: RemoteWorkspace,
    agent: TerminalAgent | None,
    project,  # Project | None — typed as Any to avoid a circular import
) -> dict:
    """Build a workspace response dict from already-fetched related entities.

    Synchronous on purpose so it can be reused inside batched loops without
    triggering extra DB round-trips.
    """
    return {
        "id": str(w.id),
        "agent_id": w.agent_id,
        "agent_name": agent.name if agent else "",
        "project_id": w.project_id,
        "project_name": project.name if project else "",
        "remote_path": w.remote_path,
        "label": w.label,
        "is_online": agent_manager.is_connected(w.agent_id),
        "created_at": w.created_at.isoformat(),
        "updated_at": w.updated_at.isoformat(),
    }


def to_object_ids(ids: list[str]) -> list[ObjectId]:
    """Convert string IDs to ObjectId, silently dropping invalid ones."""
    out: list[ObjectId] = []
    for s in ids:
        try:
            out.append(ObjectId(s))
        except (InvalidId, TypeError):
            continue
    return out
