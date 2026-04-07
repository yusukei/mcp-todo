"""Helpers for cascading approve flag changes across task hierarchies."""

from __future__ import annotations

import asyncio

from ..models import Task
from ..services.events import publish_event
from ..services.search import index_task as _index_task
from ..services.serializers import task_to_dict as _task_dict


async def cascade_approve_subtasks(parent_task_id: str, actor: str) -> list[Task]:
    """Recursively approve every (non-deleted, not yet approved) descendant of a task.

    Walks the parent_task_id graph breadth-first and flips ``approved=True`` /
    ``needs_detail=False`` on each descendant. Tasks that are already approved
    are skipped (no change recorded). Saves and reindexes mutated tasks in
    parallel and emits a single ``tasks.batch_updated`` event per project.

    Args:
        parent_task_id: ID of the task whose subtree should be approved.
        actor: Identifier (user id, ``mcp:<key>``, etc.) recorded on the
            change history of each cascaded task.

    Returns:
        The list of Task objects that were actually modified (after save).
    """
    visited: set[str] = set()
    pending: list[str] = [parent_task_id]
    mutated: list[Task] = []

    while pending:
        # Fetch direct children of all pending parents in one query
        children = await Task.find(
            {"parent_task_id": {"$in": pending}, "is_deleted": False},
        ).to_list()
        next_layer: list[str] = []

        for child in children:
            cid = str(child.id)
            if cid in visited:
                continue
            visited.add(cid)
            next_layer.append(cid)

            if not child.approved:
                child.record_change("approved", str(child.approved), "True", actor)
                child.approved = True
                child.needs_detail = False
                mutated.append(child)

        pending = next_layer

    if not mutated:
        return []

    results = await asyncio.gather(
        *[t.save_updated() for t in mutated],
        return_exceptions=True,
    )

    saved: list[Task] = []
    project_ids: set[str] = set()
    for task, result in zip(mutated, results):
        if isinstance(result, Exception):
            continue
        saved.append(task)
        project_ids.add(task.project_id)
        await _index_task(task)

    for pid in project_ids:
        ids = [str(t.id) for t in saved if t.project_id == pid]
        await publish_event(pid, "tasks.batch_updated", {
            "count": len(ids),
            "task_ids": ids,
        })

    return saved
