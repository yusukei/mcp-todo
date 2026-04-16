"""REST API for the error tracker Web UI (T9).

Mirrors a subset of the MCP tools but authenticated via the
standard cookie/Bearer user session (``get_current_user``) used
by the rest of the project. The ingest endpoint lives in
``app.api.error_tracker_ingest`` because it uses DSN auth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ....core.deps import get_current_user
from ....models import User
from ....models.error_tracker import (
    ErrorAuditLog,
    ErrorIssue,
    ErrorTrackingConfig,
    IssueStatus,
)
from ....services.error_tracker.events import get_event_collection_for_date

router = APIRouter(prefix="/error-tracker", tags=["error-tracker"])


# ── helpers ───────────────────────────────────────────────────


def _user_supplied(value: str | None) -> dict[str, Any]:
    return {"_user_supplied": True, "value": value or ""}


def _issue_dict(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id") or doc.get("id") or ""),
        "project_id": doc.get("project_id"),
        "error_project_id": doc.get("error_project_id"),
        "fingerprint": doc.get("fingerprint"),
        "title": _user_supplied(doc.get("title")),
        "culprit": _user_supplied(doc.get("culprit")),
        "level": doc.get("level"),
        "status": doc.get("status"),
        "first_seen": doc.get("first_seen"),
        "last_seen": doc.get("last_seen"),
        "event_count": doc.get("event_count", 0),
        "user_count": doc.get("user_count", 0),
        "release": doc.get("release"),
        "environment": doc.get("environment"),
        "assignee_id": doc.get("assignee_id"),
        "linked_task_ids": doc.get("linked_task_ids", []),
        "tags": doc.get("tags", {}),
    }


async def _member_of_project(pid: str, user: User) -> bool:
    from ....models import Project

    proj = await Project.get(pid)
    if proj is None:
        return False
    return proj.has_member(str(user.id))


async def _auth_ep(ep_id: str, user: User) -> ErrorTrackingConfig:
    ep = await ErrorTrackingConfig.get(ep_id)
    if ep is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(ep.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return ep


# ── Projects (admin) ──────────────────────────────────────────


class ProjectSettingsIn(BaseModel):
    allowed_origins: list[str] | None = None
    allowed_origin_wildcard: bool | None = None
    rate_limit_per_min: int | None = Field(None, ge=1, le=100_000)
    retention_days: int | None = Field(None, ge=1, le=90)
    scrub_ip: bool | None = None
    auto_create_task_on_new_issue: bool | None = None


@router.get("/projects")
async def list_error_projects(user: User = Depends(get_current_user)) -> list[dict]:
    from ....models import Project

    my_projects = [
        p for p in await Project.find_all().to_list()
        if p.has_member(str(user.id))
    ]
    ids = [str(p.id) for p in my_projects]
    if not ids:
        return []
    cursor = ErrorTrackingConfig.find({"project_id": {"$in": ids}})
    out: list[dict] = []
    async for ep in cursor:
        out.append(
            {
                "id": str(ep.id),
                "project_id": ep.project_id,
                "name": ep.name,
                "allowed_origins": ep.allowed_origins,
                "allowed_origin_wildcard": ep.allowed_origin_wildcard,
                "rate_limit_per_min": ep.rate_limit_per_min,
                "retention_days": ep.retention_days,
                "scrub_ip": ep.scrub_ip,
                "auto_create_task_on_new_issue": ep.auto_create_task_on_new_issue,
                "enabled": ep.enabled,
                "keys": [
                    {
                        "public_key": k.public_key,
                        "secret_key_prefix": k.secret_key_prefix,
                        "expire_at": k.expire_at,
                        "created_at": k.created_at,
                    }
                    for k in ep.keys
                ],
            }
        )
    return out


@router.patch("/projects/{error_project_id}")
async def update_error_project(
    error_project_id: str,
    body: ProjectSettingsIn,
    user: User = Depends(get_current_user),
) -> dict:
    ep = await _auth_ep(error_project_id, user)
    fields = body.model_dump(exclude_none=True)
    for k, v in fields.items():
        setattr(ep, k, v)
    await ep.save_updated()
    await ErrorAuditLog(
        project_id=ep.project_id,
        error_project_id=str(ep.id),
        action="update_settings",
        actor_id=str(user.id),
        actor_kind="user",
        details=fields,
    ).insert()
    return {"id": str(ep.id), **fields}


# ── Issues ────────────────────────────────────────────────────


@router.get("/projects/{error_project_id}/issues")
async def list_issues(
    error_project_id: str,
    status: str | None = Query(None),
    environment: str | None = Query(None),
    release: str | None = Query(None),
    limit: int = Query(50, ge=1),
    user: User = Depends(get_current_user),
) -> list[dict]:
    ep = await _auth_ep(error_project_id, user)
    q: dict[str, Any] = {"project_id": ep.project_id}
    if status:
        try:
            IssueStatus(status)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "validation_error", "message": f"bad status: {status}"},
            ) from exc
        q["status"] = status
    if environment:
        q["environment"] = environment
    if release:
        q["release"] = release
    cur = ErrorIssue.get_motor_collection().find(q).sort("last_seen", -1).limit(limit)
    return [_issue_dict(doc) async for doc in cur]


@router.get("/issues/{issue_id}")
async def get_issue(
    issue_id: str, user: User = Depends(get_current_user)
) -> dict:
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(issue.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return _issue_dict(issue.model_dump(by_alias=True))


@router.get("/issues/{issue_id}/events")
async def list_issue_events(
    issue_id: str,
    limit: int = Query(20, ge=1),
    user: User = Depends(get_current_user),
) -> list[dict]:
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(issue.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    now = datetime.now(UTC)
    results: list[dict] = []
    for offset in range(0, 3):
        coll = await get_event_collection_for_date(now - timedelta(days=offset))
        cur = (
            coll.find({"issue_id": str(issue.id)})
            .sort("received_at", -1)
            .limit(limit)
        )
        async for ev in cur:
            ev["id"] = str(ev.pop("_id", ""))
            ev["message"] = _user_supplied(ev.get("message"))
            results.append(ev)
            if len(results) >= limit:
                return results
    return results


class IssueUpdateIn(BaseModel):
    assignee_id: str | None = None


@router.patch("/issues/{issue_id}")
async def update_issue(
    issue_id: str,
    body: IssueUpdateIn,
    user: User = Depends(get_current_user),
) -> dict:
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(issue.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    issue.assignee_id = body.assignee_id
    issue.updated_at = datetime.now(UTC)
    await issue.save()
    await ErrorAuditLog(
        project_id=issue.project_id,
        error_project_id=issue.error_project_id,
        action="update_issue",
        actor_id=str(user.id),
        actor_kind="user",
        details={"issue_id": str(issue.id), "assignee_id": body.assignee_id},
    ).insert()
    return _issue_dict(issue.model_dump(by_alias=True))


class IssueActionIn(BaseModel):
    resolution: str | None = Field(None, max_length=500)
    until: str | None = None


@router.post("/issues/{issue_id}/resolve")
async def resolve_issue(
    issue_id: str,
    body: IssueActionIn,
    user: User = Depends(get_current_user),
) -> dict:
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(issue.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    issue.status = IssueStatus.resolved
    issue.resolution = body.resolution
    issue.updated_at = datetime.now(UTC)
    await issue.save()
    return _issue_dict(issue.model_dump(by_alias=True))


@router.post("/issues/{issue_id}/ignore")
async def ignore_issue(
    issue_id: str,
    body: IssueActionIn,
    user: User = Depends(get_current_user),
) -> dict:
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(issue.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    issue.status = IssueStatus.ignored
    if body.until:
        try:
            issue.ignored_until = datetime.fromisoformat(
                body.until.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "validation_error"},
            ) from exc
    issue.updated_at = datetime.now(UTC)
    await issue.save()
    return _issue_dict(issue.model_dump(by_alias=True))


@router.post("/issues/{issue_id}/reopen")
async def reopen_issue(
    issue_id: str, user: User = Depends(get_current_user)
) -> dict:
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(issue.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    issue.status = IssueStatus.unresolved
    issue.resolution = None
    issue.ignored_until = None
    issue.updated_at = datetime.now(UTC)
    await issue.save()
    return _issue_dict(issue.model_dump(by_alias=True))


@router.get("/issues/{issue_id}/histogram")
async def issue_histogram(
    issue_id: str,
    period: str = Query("24h", pattern=r"^\d+[hd]$"),
    interval: str = Query("1h", pattern=r"^\d+[hm]$"),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Return event counts bucketed by time interval for an issue."""
    issue = await ErrorIssue.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if not await _member_of_project(issue.project_id, user):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})

    # Parse period/interval
    def _parse_duration(s: str) -> timedelta:
        val = int(s[:-1])
        unit = s[-1]
        if unit == "h":
            return timedelta(hours=val)
        if unit == "d":
            return timedelta(days=val)
        if unit == "m":
            return timedelta(minutes=val)
        return timedelta(hours=1)

    period_td = _parse_duration(period)
    interval_td = _parse_duration(interval)

    now = datetime.now(UTC)
    start = now - period_td
    interval_secs = int(interval_td.total_seconds())

    # Collect events from relevant daily partitions
    counts: dict[int, int] = {}  # bucket_ts -> count
    days_back = (now.date() - start.date()).days + 1
    for offset in range(days_back + 1):
        d = now - timedelta(days=offset)
        try:
            coll = await get_event_collection_for_date(d)
        except Exception:
            continue
        pipeline = [
            {"$match": {
                "issue_id": str(issue.id),
                "received_at": {"$gte": start.isoformat(), "$lte": now.isoformat()},
            }},
            {"$project": {"received_at": 1}},
        ]
        async for doc in coll.aggregate(pipeline):
            ts_raw = doc.get("received_at")
            if not ts_raw:
                continue
            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                continue
            bucket = int(ts.timestamp()) // interval_secs * interval_secs
            counts[bucket] = counts.get(bucket, 0) + 1

    # Fill empty buckets
    result: list[dict] = []
    bucket = int(start.timestamp()) // interval_secs * interval_secs
    end_ts = int(now.timestamp())
    while bucket <= end_ts:
        result.append({
            "timestamp": datetime.fromtimestamp(bucket, tz=UTC).isoformat(),
            "count": counts.get(bucket, 0),
        })
        bucket += interval_secs

    return result


__all__ = ["router"]
