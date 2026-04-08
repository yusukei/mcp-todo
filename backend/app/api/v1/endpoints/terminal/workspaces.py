"""Workspace CRUD admin endpoints."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from .....core.deps import get_admin_user
from .....models import User
from .....models.terminal import RemoteWorkspace, TerminalAgent
from ._shared import (
    WorkspaceCreateRequest,
    WorkspaceUpdateRequest,
    build_workspace_dict,
    to_object_ids,
)

router = APIRouter()


@router.get("/workspaces")
async def list_workspaces(user: User = Depends(get_admin_user)) -> list[dict]:
    """List all workspaces with their agent / project details.

    Performs at most three database queries regardless of workspace count:
      1. RemoteWorkspace.find_all()
      2. TerminalAgent.find({_id: {$in: [...]}})
      3. Project.find({_id: {$in: [...]}})
    """
    workspaces = await RemoteWorkspace.find_all().sort("-created_at").to_list()
    if not workspaces:
        return []

    # Collect unique foreign-key strings, preserve dedup so the $in queries
    # don't ship duplicates to MongoDB.
    agent_id_strs = {w.agent_id for w in workspaces if w.agent_id}
    project_id_strs = {w.project_id for w in workspaces if w.project_id}

    from .....models import Project

    agent_oids = to_object_ids(list(agent_id_strs))
    project_oids = to_object_ids(list(project_id_strs))

    agents_task = (
        TerminalAgent.find({"_id": {"$in": agent_oids}}).to_list()
        if agent_oids
        else asyncio.sleep(0, result=[])
    )
    projects_task = (
        Project.find({"_id": {"$in": project_oids}}).to_list()
        if project_oids
        else asyncio.sleep(0, result=[])
    )
    agents, projects = await asyncio.gather(agents_task, projects_task)

    agent_by_id: dict[str, TerminalAgent] = {str(a.id): a for a in agents}
    project_by_id: dict[str, object] = {str(p.id): p for p in projects}

    return [
        build_workspace_dict(
            w,
            agent_by_id.get(w.agent_id),
            project_by_id.get(w.project_id),
        )
        for w in workspaces
    ]


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    # Validate agent exists and belongs to user
    agent = await TerminalAgent.get(body.agent_id)
    if not agent or agent.owner_id != str(user.id):
        raise HTTPException(status_code=404, detail="Agent not found")

    # Validate project exists
    from .....models import Project
    project = await Project.get(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check uniqueness (1 project = 1 workspace)
    existing = await RemoteWorkspace.find_one({"project_id": body.project_id})
    if existing:
        raise HTTPException(status_code=409, detail="Project already has a workspace")

    workspace = RemoteWorkspace(
        agent_id=body.agent_id,
        project_id=body.project_id,
        remote_path=body.remote_path,
        label=body.label,
    )
    await workspace.insert()
    # Reuse the already-fetched agent/project to avoid two extra round-trips.
    return build_workspace_dict(workspace, agent, project)


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    user: User = Depends(get_admin_user),
) -> dict:
    workspace = await RemoteWorkspace.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if body.remote_path is not None:
        workspace.remote_path = body.remote_path
    if body.label is not None:
        workspace.label = body.label
    workspace.updated_at = datetime.now(UTC)
    await workspace.save()
    # Fetch related entities for the single-workspace response.
    agent = await TerminalAgent.get(workspace.agent_id)
    from .....models import Project
    project = await Project.get(workspace.project_id)
    return build_workspace_dict(workspace, agent, project)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(workspace_id: str, user: User = Depends(get_admin_user)) -> None:
    workspace = await RemoteWorkspace.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await workspace.delete()
