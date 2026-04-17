"""Cross-task dependency (blocks / blocked_by) helpers.

Separates hierarchical relationships (``parent_task_id``) from lateral
dependencies (``blocks`` / ``blocked_by``). All operations are scoped to a
single ``project_id`` — cross-project links are rejected by validation in the
API / MCP layer.

This module currently provides cycle detection (S1-3). The link / unlink /
cleanup_dependents helpers are added in S1-2 / S1-5 on top of these primitives.
"""

from __future__ import annotations

import asyncio

from ..models import Task


class TaskLinkError(Exception):
    """Base class for link-related errors.

    Subclasses expose a ``code`` string suitable for machine-readable API
    responses (e.g. ``"cycle_detected"``). Callers should translate into
    HTTP status codes at the boundary.
    """

    code: str = "task_link_error"

    def __init__(self, message: str = "", **details) -> None:
        super().__init__(message)
        self.details: dict = details


class SelfReferenceError(TaskLinkError):
    code = "self_reference"


class CrossProjectError(TaskLinkError):
    code = "cross_project"


class TargetNotFoundError(TaskLinkError):
    code = "target_not_found"


class DuplicateLinkError(TaskLinkError):
    code = "duplicate_link"


class LinkNotFoundError(TaskLinkError):
    code = "link_not_found"


class CycleError(TaskLinkError):
    """A proposed link would create a cycle in the blocks graph.

    ``details["path"]`` contains the existing path target → ... → source
    (which, combined with the proposed source → target edge, closes the loop).
    """

    code = "cycle_detected"


async def has_cycle(
    project_id: str,
    source_id: str,
    target_id: str,
) -> list[str] | None:
    """Return the cycle path if adding ``source --blocks--> target`` would cycle.

    A new edge source → target creates a cycle iff there is already a path
    target → ... → source in the blocks graph. We BFS from ``target_id`` along
    ``blocks`` edges; if we ever reach ``source_id`` we reconstruct and return
    the path ``[target_id, ..., source_id]``.

    Self-reference (``source_id == target_id``) is treated as a trivial cycle
    and returns ``[source_id]`` without a database round-trip.

    Args:
        project_id: Project scope. Only tasks within this project are traversed.
        source_id: Task that would gain the outgoing ``blocks`` edge.
        target_id: Task that would gain the incoming ``blocked_by`` edge.

    Returns:
        ``None`` when no cycle would form; otherwise the list of task IDs
        traversed from ``target_id`` up to ``source_id`` (inclusive).

    Complexity:
        O(V + E) within the project's blocks graph — each task and each edge
        is visited at most once. Queries are issued per BFS layer so wide-and-
        shallow graphs resolve in O(depth) round-trips.
    """
    if source_id == target_id:
        return [source_id]

    visited: set[str] = {target_id}
    predecessor: dict[str, str] = {}
    frontier: list[str] = [target_id]

    while frontier:
        # Fetch only the ``blocks`` field for the current layer — we do not
        # need the full document, and this keeps the round-trip cheap for
        # projects with large tasks.
        col = Task.get_motor_collection()
        cursor = col.find(
            {
                "project_id": project_id,
                "is_deleted": False,
                "_id": {"$in": [_as_object_id(tid) for tid in frontier]},
            },
            projection={"blocks": 1},
        )

        next_frontier: list[str] = []
        async for doc in cursor:
            current_id = str(doc["_id"])
            for neighbor_id in doc.get("blocks", []) or []:
                if neighbor_id == source_id:
                    # Close the loop: target → ... → current → source.
                    path = [source_id, current_id]
                    cur = current_id
                    while cur in predecessor:
                        cur = predecessor[cur]
                        path.append(cur)
                    path.reverse()
                    return path
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                predecessor[neighbor_id] = current_id
                next_frontier.append(neighbor_id)

        frontier = next_frontier

    return None


async def link(
    source_id: str,
    target_id: str,
    relation: str = "blocks",
    changed_by: str = "",
) -> tuple[Task, Task]:
    """Create a ``source --blocks--> target`` edge with bidirectional sync.

    Validates identity, cross-project, existence, duplicates, and cycles
    before mutating either side. Both tasks are saved; on save failure the
    first successful write remains persisted (caller is responsible for
    surfacing the error — partial writes are rare with mongomock/Motor and
    acceptable for the current scale).

    Args:
        source_id: Task that will gain the outgoing ``blocks`` entry.
        target_id: Task that will gain the incoming ``blocked_by`` entry.
        relation: Reserved for future relation types; only ``"blocks"`` is
            currently supported. Other values raise ``ValueError``.
        changed_by: Actor identifier recorded on both tasks' activity logs.

    Returns:
        ``(updated_source, updated_target)`` after a successful save.

    Raises:
        SelfReferenceError, TargetNotFoundError, CrossProjectError,
        DuplicateLinkError, CycleError.
    """
    if relation != "blocks":
        raise ValueError(f"Unsupported relation: {relation!r}")

    if source_id == target_id:
        raise SelfReferenceError("A task cannot block itself", task_id=source_id)

    source = await Task.get(source_id)
    target = await Task.get(target_id)
    if not source or source.is_deleted:
        raise TargetNotFoundError("Source task not found", task_id=source_id)
    if not target or target.is_deleted:
        raise TargetNotFoundError("Target task not found", task_id=target_id)
    if source.project_id != target.project_id:
        raise CrossProjectError(
            "Cross-project links are not supported",
            source_project=source.project_id,
            target_project=target.project_id,
        )

    if target_id in source.blocks:
        raise DuplicateLinkError(
            "Link already exists",
            source_id=source_id,
            target_id=target_id,
        )

    cycle_path = await has_cycle(source.project_id, source_id, target_id)
    if cycle_path is not None:
        raise CycleError("Adding this link would create a cycle", path=cycle_path)

    old_blocks = list(source.blocks)
    old_blocked_by = list(target.blocked_by)
    source.blocks = old_blocks + [target_id]
    # Keep ``blocked_by`` idempotent — normally empty of source_id given the
    # duplicate check above, but resilient to any prior half-applied state.
    if source_id not in target.blocked_by:
        target.blocked_by = old_blocked_by + [source_id]

    source.record_change("blocks", str(old_blocks), str(source.blocks), changed_by)
    target.record_change("blocked_by", str(old_blocked_by), str(target.blocked_by), changed_by)

    await asyncio.gather(source.save_updated(), target.save_updated())
    return source, target


async def unlink(
    source_id: str,
    target_id: str,
    relation: str = "blocks",
    changed_by: str = "",
) -> tuple[Task, Task]:
    """Remove the ``source --blocks--> target`` edge from both tasks.

    Raises ``LinkNotFoundError`` when the edge is absent on either side;
    callers should treat this as an idempotent no-op error (404) rather than
    server state corruption.
    """
    if relation != "blocks":
        raise ValueError(f"Unsupported relation: {relation!r}")

    source = await Task.get(source_id)
    target = await Task.get(target_id)
    if not source or source.is_deleted or not target or target.is_deleted:
        raise LinkNotFoundError(
            "Link not found (task missing or deleted)",
            source_id=source_id,
            target_id=target_id,
        )
    if target_id not in source.blocks and source_id not in target.blocked_by:
        raise LinkNotFoundError(
            "Link not found",
            source_id=source_id,
            target_id=target_id,
        )

    old_blocks = list(source.blocks)
    old_blocked_by = list(target.blocked_by)
    source.blocks = [t for t in source.blocks if t != target_id]
    target.blocked_by = [s for s in target.blocked_by if s != source_id]

    if old_blocks != source.blocks:
        source.record_change("blocks", str(old_blocks), str(source.blocks), changed_by)
    if old_blocked_by != target.blocked_by:
        target.record_change("blocked_by", str(old_blocked_by), str(target.blocked_by), changed_by)

    await asyncio.gather(source.save_updated(), target.save_updated())
    return source, target


async def list_dependents(project_id: str, task_id: str) -> list[Task]:
    """Return tasks that list ``task_id`` in their ``blocked_by`` within the project.

    Used by ``delete_task`` (S1-5) to check if deletion would orphan dangling
    references. Excludes soft-deleted tasks.
    """
    return await Task.find(
        {
            "project_id": project_id,
            "is_deleted": False,
            "blocked_by": {"$in": [task_id]},
        }
    ).to_list()


async def cleanup_dependents(task_id: str, project_id: str, changed_by: str = "") -> list[Task]:
    """Remove ``task_id`` from every ``blocked_by`` list in the project.

    Mirrors ``task_approval.cascade_approve_subtasks`` in structure: locates
    affected tasks, mutates them, saves in parallel, returns the list that
    was actually modified. The caller is responsible for publishing
    ``task.updated`` events for each returned task (or a single batch event).
    """
    dependents = await list_dependents(project_id, task_id)
    if not dependents:
        return []

    for dep in dependents:
        old = list(dep.blocked_by)
        dep.blocked_by = [b for b in dep.blocked_by if b != task_id]
        dep.record_change("blocked_by", str(old), str(dep.blocked_by), changed_by)

    await asyncio.gather(*[d.save_updated() for d in dependents])
    return dependents


def _as_object_id(task_id: str):
    """Coerce a string task id to ObjectId when possible.

    Beanie stores ``_id`` as :class:`bson.ObjectId`; queries by string fail
    silently on real MongoDB (mongomock is more permissive). This helper
    falls back to the raw string for tests or non-ObjectId keys so the
    module stays environment-agnostic.
    """
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        return ObjectId(task_id)
    except (InvalidId, TypeError):
        return task_id
