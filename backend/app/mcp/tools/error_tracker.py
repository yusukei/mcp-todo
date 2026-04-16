"""MCP tools for the Sentry-compatible error tracker (T8).

Exposes 14 tools (spec §6.2 + v3 decision #2):

    list_error_issues / get_error_issue / list_error_events /
    get_error_event / resolve_error_issue / ignore_error_issue /
    reopen_error_issue / link_error_to_task / unlink_error_from_task /
    create_task_from_error / get_error_stats / create_error_project /
    rotate_error_dsn / configure_error_auto_task

Prompt-injection safety (§6.1): the ``title`` / ``message`` /
``culprit`` fields are wrapped in ``{"_user_supplied": true,
"value": ...}`` so LLM prompts can mark the region untrusted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastmcp.exceptions import ToolError

from ...models.error_tracker import (
    AutoTaskPriority,
    DsnKeyRecord,
    ErrorAuditLog,
    ErrorIssue,
    ErrorTrackingConfig,
    IssueStatus,
)
from ...services.error_tracker.auto_task import create_task_for_new_issue
from ...services.error_tracker.events import get_event_collection_for_date
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _resolve_project_id

logger = logging.getLogger(__name__)


# ── Argument parsers ──────────────────────────────────────────

_ALLOWED_PERIODS = {"1h", "24h", "7d", "30d"}
_ALLOWED_GROUPBY = {"environment", "release", "level", "browser"}


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    # Absolute ISO 8601.
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    # Relative: ``<N><unit>`` where unit ∈ {m,h,d}.
    if len(v) >= 2 and v[-1] in ("m", "h", "d") and v[:-1].isdigit():
        n = int(v[:-1])
        now = datetime.now(UTC)
        if v[-1] == "m":
            return now - timedelta(minutes=n)
        if v[-1] == "h":
            return now - timedelta(hours=n)
        return now - timedelta(days=n)
    raise ToolError(f"Unrecognised since format: {value!r}")


def _user_supplied(value: str | None) -> dict[str, Any]:
    return {"_user_supplied": True, "value": value or ""}


def _issue_to_dict(issue: ErrorIssue | dict[str, Any]) -> dict[str, Any]:
    if isinstance(issue, dict):
        data = issue
    else:
        data = issue.model_dump()
        data["id"] = str(issue.id)
    return {
        "id": str(data.get("_id") or data.get("id") or ""),
        "project_id": data.get("project_id"),
        "error_project_id": data.get("error_project_id"),
        "fingerprint": data.get("fingerprint"),
        "title": _user_supplied(data.get("title")),
        "culprit": _user_supplied(data.get("culprit")),
        "level": data.get("level"),
        "status": data.get("status"),
        "first_seen": data.get("first_seen"),
        "last_seen": data.get("last_seen"),
        "event_count": data.get("event_count", 0),
        "user_count": data.get("user_count", 0),
        "release": data.get("release"),
        "environment": data.get("environment"),
        "assignee_id": data.get("assignee_id"),
        "linked_task_ids": data.get("linked_task_ids", []),
        "tags": data.get("tags", {}),
    }


async def _resolve_error_project(project_id: str, key_info: dict) -> ErrorTrackingConfig:
    pid = await _resolve_project_id(project_id)
    await check_project_access(pid, key_info)
    ep = await ErrorTrackingConfig.find_one(ErrorTrackingConfig.project_id == pid)
    if ep is None:
        raise ToolError(f"Error tracker is not enabled for project {pid}")
    return ep


# ── Tools ─────────────────────────────────────────────────────


@mcp.tool()
async def list_error_issues(
    project_id: str,
    status: str | None = None,
    environment: str | None = None,
    release: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List error Issues in a project, most recent first.

    Args:
        project_id: Project ID or name
        status: unresolved / resolved / ignored (default: all)
        environment: optional env filter
        release: optional release filter
        since: ISO 8601 or relative (``7d``, ``24h``, ``30m``)
        limit: max rows (1-100)
    """
    key_info = await authenticate()
    ep = await _resolve_error_project(project_id, key_info)
    q: dict[str, Any] = {"project_id": ep.project_id}
    if status:
        try:
            IssueStatus(status)
        except ValueError as exc:
            raise ToolError(f"Invalid status {status!r}") from exc
        q["status"] = status
    if environment:
        q["environment"] = environment
    if release:
        q["release"] = release
    since_dt = _parse_since(since)
    if since_dt:
        q["last_seen"] = {"$gte": since_dt}
    lim = max(1, min(int(limit or 20), 100))
    coll = ErrorIssue.get_motor_collection()
    cur = coll.find(q).sort("last_seen", -1).limit(lim)
    return [_issue_to_dict(doc) async for doc in cur]


@mcp.tool()
async def get_error_issue(issue_id: str) -> dict:
    """Return a single Issue by id, with its most recent event summary.

    Args:
        issue_id: Issue ID (24-char hex ObjectId) or fingerprint prefix
    """
    await authenticate()
    issue = None
    # Try ObjectId first
    try:
        issue = await ErrorIssue.get(issue_id)
    except Exception:
        pass
    # Fallback: fingerprint prefix match
    if issue is None:
        issue = await ErrorIssue.find_one({"fingerprint": {"$regex": f"^{issue_id}"}})
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    out = _issue_to_dict(issue)
    # Attach a slim summary of the newest event.
    coll = await get_event_collection_for_date(issue.last_seen or datetime.now(UTC))
    ev = await coll.find_one(
        {"issue_id": str(issue.id)}, sort=[("received_at", -1)]
    )
    if ev:
        out["latest_event"] = {
            "event_id": ev.get("event_id"),
            "received_at": ev.get("received_at"),
            "message": _user_supplied(ev.get("message")),
            "exception_type": (
                ((ev.get("exception") or {}).get("values") or [{}])[0].get("type")
            ),
        }
    return out


@mcp.tool()
async def list_error_events(issue_id: str, limit: int = 10) -> list[dict]:
    """List recent event occurrences (metadata only) for an Issue."""
    await authenticate()
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    lim = max(1, min(int(limit or 10), 100))
    # Look at up to two recent days.
    now = datetime.now(UTC)
    results: list[dict] = []
    for offset in (0, 1):
        day = now - timedelta(days=offset)
        coll = await get_event_collection_for_date(day)
        cur = coll.find({"issue_id": str(issue.id)}).sort("received_at", -1).limit(lim)
        async for ev in cur:
            results.append(
                {
                    "event_id": ev.get("event_id"),
                    "received_at": ev.get("received_at"),
                    "level": ev.get("level"),
                    "release": ev.get("release"),
                    "environment": ev.get("environment"),
                }
            )
            if len(results) >= lim:
                return results
    return results


@mcp.tool()
async def get_error_event(event_id: str, project_id: str) -> dict:
    """Return full stack / breadcrumbs for a specific event."""
    key_info = await authenticate()
    pid = await _resolve_project_id(project_id)
    await check_project_access(pid, key_info)
    now = datetime.now(UTC)
    # Scan up to 30 days (spec default retention); early exit on first hit.
    for offset in range(0, 31):
        coll = await get_event_collection_for_date(now - timedelta(days=offset))
        ev = await coll.find_one({"project_id": pid, "event_id": event_id})
        if ev:
            ev["id"] = str(ev.pop("_id", ""))
            # Wrap user-supplied strings.
            ev["message"] = _user_supplied(ev.get("message"))
            return ev
    raise ToolError(f"Event not found: {event_id}")


@mcp.tool()
async def resolve_error_issue(issue_id: str, resolution: str | None = None) -> dict:
    """Mark an Issue resolved. Optional ``resolution`` note (max 500 chars)."""
    await authenticate()
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    issue.status = IssueStatus.resolved
    issue.resolution = (resolution or "")[:500] or None
    issue.updated_at = datetime.now(UTC)
    await issue.save()
    return _issue_to_dict(issue)


@mcp.tool()
async def ignore_error_issue(issue_id: str, until: str | None = None) -> dict:
    """Mark an Issue ignored (optionally until a given ISO timestamp)."""
    await authenticate()
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    issue.status = IssueStatus.ignored
    if until:
        try:
            issue.ignored_until = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolError(f"Invalid ISO 8601: {until!r}") from exc
    issue.updated_at = datetime.now(UTC)
    await issue.save()
    return _issue_to_dict(issue)


@mcp.tool()
async def reopen_error_issue(issue_id: str) -> dict:
    """Move an Issue back to ``unresolved``."""
    await authenticate()
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    issue.status = IssueStatus.unresolved
    issue.resolution = None
    issue.ignored_until = None
    issue.updated_at = datetime.now(UTC)
    await issue.save()
    return _issue_to_dict(issue)


@mcp.tool()
async def link_error_to_task(issue_id: str, task_id: str) -> dict:
    """Attach an Issue to an existing Task (idempotent)."""
    await authenticate()
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    if task_id not in issue.linked_task_ids:
        issue.linked_task_ids.append(task_id)
        issue.updated_at = datetime.now(UTC)
        await issue.save()
    return _issue_to_dict(issue)


@mcp.tool()
async def unlink_error_from_task(issue_id: str, task_id: str) -> dict:
    """Detach an Issue from a Task (idempotent)."""
    await authenticate()
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    if task_id in issue.linked_task_ids:
        issue.linked_task_ids.remove(task_id)
        issue.updated_at = datetime.now(UTC)
        await issue.save()
    return {"ok": True, "issue_id": issue_id, "task_id": task_id}


@mcp.tool()
async def create_task_from_error(
    issue_id: str,
    title: str | None = None,
    priority: str | None = None,
) -> dict:
    """Spawn a new mcp-todo Task from an Issue with prompt-injection-safe body."""
    await authenticate()
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise ToolError(f"Issue not found: {issue_id}")
    ep = await ErrorTrackingConfig.find_one(ErrorTrackingConfig.project_id == issue.project_id)
    if ep is None:
        raise ToolError(f"Error project missing for {issue.project_id}")
    # Temporarily override title/priority if the caller specified them.
    if title:
        issue.title = title[:255]
    if priority:
        try:
            ep.auto_task_priority = AutoTaskPriority(priority)
        except ValueError as exc:
            raise ToolError(f"Invalid priority {priority!r}") from exc
    tid = await create_task_for_new_issue(ep, issue)
    return {"task_id": tid, "issue_id": issue_id}


@mcp.tool()
async def get_error_stats(
    project_id: str, period: str = "7d", group_by: str | None = None
) -> dict:
    """Return event totals over a period, optionally grouped."""
    key_info = await authenticate()
    ep = await _resolve_error_project(project_id, key_info)
    if period not in _ALLOWED_PERIODS:
        raise ToolError(f"period must be one of {sorted(_ALLOWED_PERIODS)}")
    if group_by and group_by not in _ALLOWED_GROUPBY:
        raise ToolError(f"group_by must be one of {sorted(_ALLOWED_GROUPBY)}")

    since = _parse_since(period) or datetime.now(UTC) - timedelta(days=7)
    match = {"project_id": ep.project_id, "last_seen": {"$gte": since}}
    coll = ErrorIssue.get_motor_collection()
    pipeline: list[dict[str, Any]] = [{"$match": match}]
    if group_by:
        pipeline.append(
            {
                "$group": {
                    "_id": f"${group_by}",
                    "issues": {"$sum": 1},
                    "events": {"$sum": "$event_count"},
                }
            }
        )
    else:
        pipeline.append(
            {
                "$group": {
                    "_id": None,
                    "issues": {"$sum": 1},
                    "events": {"$sum": "$event_count"},
                }
            }
        )
    rows = await coll.aggregate(pipeline).to_list(length=100)
    return {"project_id": ep.project_id, "period": period, "rows": rows}


# ── Admin tools ───────────────────────────────────────────────

from ...services.error_tracker.provision import _generate_dsn_pair  # noqa: E402


@mcp.tool()
async def rotate_error_dsn(
    error_project_id: str,
    keep_old_for_minutes: int = 5,
) -> dict:
    """Rotate the DSN. Returns the new DSN; the old key keeps working for the grace window."""
    key_info = await authenticate()
    ep = await ErrorTrackingConfig.get(error_project_id)
    if ep is None:
        raise ToolError(f"Error project not found: {error_project_id}")
    await check_project_access(ep.project_id, key_info)

    grace = max(0, int(keep_old_for_minutes or 0))
    now = datetime.now(UTC)
    # Expire all currently-active keys.
    for k in ep.keys:
        if k.expire_at is None or k.expire_at > now:
            k.expire_at = now + timedelta(minutes=grace) if grace > 0 else now
    public_key, secret_key, secret_hash = _generate_dsn_pair()
    ep.keys.append(
        DsnKeyRecord(
            public_key=public_key,
            secret_key_hash=secret_hash,
            secret_key_prefix=secret_key[:8],
        )
    )
    await ep.save_updated()
    await ErrorAuditLog(
        project_id=ep.project_id,
        error_project_id=str(ep.id),
        action="rotate_dsn",
        actor_id=str(key_info.get("user_id") or ""),
        actor_kind="mcp",
        details={"new_public_key": public_key, "grace_minutes": grace},
    ).insert()
    return {
        "id": str(ep.id),
        "public_key": public_key,
        "secret_key": secret_key,
        "grace_minutes": grace,
    }


@mcp.tool()
async def configure_error_auto_task(
    error_project_id: str,
    enabled: bool | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Tune the auto-task behaviour on new Issue detection (decision #2)."""
    key_info = await authenticate()
    ep = await ErrorTrackingConfig.get(error_project_id)
    if ep is None:
        raise ToolError(f"Error project not found: {error_project_id}")
    await check_project_access(ep.project_id, key_info)
    if enabled is not None:
        ep.auto_create_task_on_new_issue = bool(enabled)
    if priority is not None:
        try:
            ep.auto_task_priority = AutoTaskPriority(priority)
        except ValueError as exc:
            raise ToolError(f"Invalid priority {priority!r}") from exc
    if assignee_id is not None:
        ep.auto_task_assignee_id = assignee_id or None
    if tags is not None:
        ep.auto_task_tags = list(tags)
    await ep.save_updated()
    return {
        "id": str(ep.id),
        "auto_create_task_on_new_issue": ep.auto_create_task_on_new_issue,
        "auto_task_priority": ep.auto_task_priority.value
        if hasattr(ep.auto_task_priority, "value")
        else str(ep.auto_task_priority),
        "auto_task_assignee_id": ep.auto_task_assignee_id,
        "auto_task_tags": ep.auto_task_tags,
    }
