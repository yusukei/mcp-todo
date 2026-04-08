"""Agent CRUD + rotate-token + check-update admin endpoints."""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status

from .....core.deps import get_admin_user
from .....core.security import hash_api_key
from .....models import User
from .....models.remote import RemoteAgent
from .....services.agent_manager import AgentOfflineError, agent_manager
from ._releases_util import (
    build_update_payload,
    find_latest_release,
    is_newer,
)
from ._shared import (
    ALLOWED_CHANNELS,
    AgentSettingsUpdateRequest,
    CreateAgentRequest,
    agent_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/agents")
async def list_agents(user: User = Depends(get_admin_user)) -> list[dict]:
    agents = await RemoteAgent.find(
        {"owner_id": str(user.id)}
    ).sort("-created_at").to_list()
    return [agent_dict(a) for a in agents]


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(body: CreateAgentRequest, user: User = Depends(get_admin_user)) -> dict:
    raw_token = f"ta_{secrets.token_hex(32)}"
    agent = RemoteAgent(
        name=body.name,
        key_hash=hash_api_key(raw_token),
        owner_id=str(user.id),
    )
    await agent.insert()
    return {**agent_dict(agent), "token": raw_token}


@router.patch("/agents/{agent_id}")
async def update_agent_settings(
    agent_id: str,
    body: AgentSettingsUpdateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    """Update auto-update flags and channel selection for an agent."""
    agent = await RemoteAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    if body.auto_update is not None:
        agent.auto_update = body.auto_update
    if body.update_channel is not None:
        if body.update_channel not in ALLOWED_CHANNELS:
            raise HTTPException(status_code=422, detail=f"Invalid channel: {body.update_channel}")
        agent.update_channel = body.update_channel
    await agent.save()
    return agent_dict(agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: User = Depends(get_admin_user)) -> None:
    agent = await RemoteAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    agent_manager.unregister(agent_id)  # Force unregister (no ws check)
    await agent.delete()


@router.post("/agents/{agent_id}/rotate-token")
async def rotate_agent_token(
    agent_id: str,
    user: User = Depends(get_admin_user),
) -> dict:
    """Issue a new token for an agent and invalidate the old one.

    The old key_hash is overwritten in MongoDB, so any subsequent
    auth attempt with the previous token will fail. If the agent is
    currently connected, its WebSocket is force-closed so it has to
    re-authenticate with the new token (operators must distribute the
    rotated token to the agent host before reconnecting).
    """
    agent = await RemoteAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")

    raw_token = f"ta_{secrets.token_hex(32)}"
    agent.key_hash = hash_api_key(raw_token)
    await agent.save()

    # Force-disconnect any live connection bound to the old token.
    if agent_manager.is_connected(agent_id):
        agent_manager.unregister(agent_id)
        agent.is_online = False
        try:
            await agent.save()
        except Exception:
            # The token has already been rotated in the DB above, so
            # losing the is_online flag here is a monitoring concern,
            # not a security one. Log with full traceback instead of
            # swallowing silently — the operator needs to know if
            # agent writes are starting to fail.
            logger.exception(
                "rotate-token: failed to persist is_online=False for agent %s",
                agent_id,
            )

    logger.info("Rotated token for agent %s (%s)", agent.name, agent_id)
    return {**agent_dict(agent), "token": raw_token}


@router.post("/agents/{agent_id}/check-update")
async def check_agent_update(
    agent_id: str,
    user: User = Depends(get_admin_user),
) -> dict:
    """Manually trigger an update_available push to a connected agent.

    Useful right after uploading a new release — instead of waiting for
    the next natural WS reconnect (which fires ``maybe_push_update``
    automatically), operators can force the check from the admin UI or
    a script. Returns ``{"pushed": false, "reason": ...}`` when no push
    was sent so callers can distinguish "not needed" from "failed".
    """
    agent = await RemoteAgent.get(agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent_manager.is_connected(agent_id):
        raise HTTPException(status_code=409, detail="Agent is not connected")
    if not agent.auto_update:
        return {"pushed": False, "reason": "auto_update disabled"}
    if not agent.os_type:
        return {"pushed": False, "reason": "agent os_type unknown"}
    latest = await find_latest_release(
        agent.os_type, agent.update_channel or "stable"
    )
    if latest is None:
        return {"pushed": False, "reason": "no release available"}
    if not is_newer(latest.version, agent.agent_version):
        return {
            "pushed": False,
            "reason": "already up to date",
            "current": agent.agent_version,
            "latest": latest.version,
        }
    payload = build_update_payload(latest)
    try:
        await agent_manager.send_raw(agent_id, payload)
    except AgentOfflineError as exc:
        raise HTTPException(status_code=409, detail="Agent disconnected during check") from exc
    except Exception as exc:
        # HTTP boundary: convert to a 500 so the admin UI sees a real
        # failure instead of a hanging request. Log with full traceback
        # (CLAUDE.md forbids ``logger.warning(..., e)`` without exc_info)
        # so operators can diagnose whether the send failure was a WS
        # issue, a serialization bug, or something else.
        logger.exception("check-update: send failed for %s", agent_id)
        raise HTTPException(status_code=500, detail=f"Push failed: {exc}") from exc
    logger.info(
        "Manual update check: pushed v%s to agent=%s (current=%s)",
        latest.version, agent_id, agent.agent_version,
    )
    return {
        "pushed": True,
        "release_id": str(latest.id),
        "version": latest.version,
        "current": agent.agent_version,
    }
