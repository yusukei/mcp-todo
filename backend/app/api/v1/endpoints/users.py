import secrets
from datetime import UTC, datetime, timedelta

from bson import DBRef, ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import DuplicateKeyError

from ....core.deps import get_admin_user, get_current_user
from ....core.validators import valid_object_id
from ....core.security import hash_password
from ....models import AllowedEmail, McpToolUsageBucket, Project, User
from ....models.mcp_api_key import McpApiKey
from ....models.user import AuthType

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserRequest(BaseModel):
    email: EmailStr
    name: str
    password: str | None = Field(None, min_length=6)
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class AllowedEmailRequest(BaseModel):
    email: EmailStr


def _user_dict(user: User, extras: dict | None = None) -> dict:
    """Serialize a User; ``extras`` carries batch-fetched admin metadata."""
    extras = extras or {}
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "auth_type": user.auth_type,
        "is_active": user.is_active,
        # Phase 0.5: lifecycle status replaces is_active for new code.
        "status": getattr(user, "status", "active"),
        "is_admin": user.is_admin,
        "picture_url": user.picture_url,
        "last_active_at": (
            user.last_active_at.isoformat() if user.last_active_at else None
        ),
        # Batch-supplied admin metadata (None for non-list responses).
        "ai_runs_30d": extras.get("ai_runs_30d"),
        "projects_count": extras.get("projects_count"),
        "created_at": user.created_at.isoformat(),
    }


async def _enrich_users_admin_meta(users: list[User]) -> dict[str, dict]:
    """Batch-fetch ``ai_runs_30d`` and ``projects_count`` for the admin
    member-list table (Phase 0.5 / API-4).

    * ``projects_count`` — number of projects whose ``members.user_id``
      matches.
    * ``ai_runs_30d`` — sum of ``McpToolUsageBucket.call_count`` over
      buckets in the last 30 days, joined to API keys via ``api_key_id``
      → ``McpApiKey.created_by`` user.
    """
    if not users:
        return {}
    user_ids = [str(u.id) for u in users]

    # ── projects_count ─────────────────────────────────────────
    proj_pipeline = [
        {"$unwind": "$members"},
        {"$match": {"members.user_id": {"$in": user_ids}}},
        {"$group": {"_id": "$members.user_id", "n": {"$sum": 1}}},
    ]
    proj_rows = await Project.get_motor_collection().aggregate(proj_pipeline).to_list(length=None)
    projects_count_map = {row["_id"]: row["n"] for row in proj_rows}

    # ── ai_runs_30d ────────────────────────────────────────────
    # Resolve api_key_id → user_id by iterating McpApiKey.created_by
    # (a Beanie ``Link[User]`` stored as DBRef). We deliberately fetch
    # all keys and filter in Python — mongomock does not support
    # ``"created_by.$id"`` style pathing reliably, and the user pool is
    # small (admin members table).
    user_id_set = set(user_ids)
    all_keys = await McpApiKey.find_all().to_list()
    api_key_to_user: dict[str, str] = {}
    for k in all_keys:
        owner_id: str = ""
        cb = k.created_by
        if hasattr(cb, "ref") and getattr(cb.ref, "id", None) is not None:
            owner_id = str(cb.ref.id)
        elif hasattr(cb, "id"):
            owner_id = str(cb.id)
        if owner_id and owner_id in user_id_set:
            api_key_to_user[str(k.id)] = owner_id

    ai_runs_count: dict[str, int] = {}
    if api_key_to_user:
        since = datetime.now(UTC) - timedelta(days=30)
        bucket_rows = (
            await McpToolUsageBucket.find(
                {
                    "api_key_id": {"$in": list(api_key_to_user.keys())},
                    "hour": {"$gte": since},
                }
            ).to_list()
        )
        for b in bucket_rows:
            owner = api_key_to_user.get(b.api_key_id or "")
            if owner:
                ai_runs_count[owner] = ai_runs_count.get(owner, 0) + b.call_count

    return {
        uid: {
            "projects_count": projects_count_map.get(uid, 0),
            "ai_runs_30d": ai_runs_count.get(uid, 0),
        }
        for uid in user_ids
    }


@router.get("")
async def list_users(
    limit: int = Query(50, ge=1),
    skip: int = Query(0, ge=0),
    _: User = Depends(get_admin_user),
) -> dict:
    query = User.find_all()
    total = await query.count()
    users = await query.skip(skip).limit(limit).to_list()
    extras_map = await _enrich_users_admin_meta(users)
    return {
        "items": [_user_dict(u, extras=extras_map.get(str(u.id))) for u in users],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(body: CreateUserRequest, admin: User = Depends(get_admin_user)) -> dict:
    password_hash = hash_password(body.password) if body.password else hash_password(secrets.token_hex(16))
    user = User(
        email=body.email,
        name=body.name,
        auth_type=AuthType.admin,
        password_hash=password_hash,
        is_admin=body.is_admin,
    )
    try:
        await user.insert()
    except DuplicateKeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    return _user_dict(user)


@router.get("/{user_id}")
async def get_user(user_id: str, _: User = Depends(get_admin_user)) -> dict:
    valid_object_id(user_id)
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # Phase 6.B-1: include the same extras the list endpoint surfaces
    # so /admin/users/:id can render the 30-day stats without a second
    # round-trip.
    extras_map = await _enrich_users_admin_meta([user])
    return _user_dict(user, extras=extras_map.get(str(user.id)))


@router.get("/{user_id}/projects")
async def list_user_projects(
    user_id: str, _: User = Depends(get_admin_user)
) -> list[dict]:
    """Phase 6.B-2: projects the given user is a member of.

    Used by the admin user-detail page. Returns id / name / color /
    member role (the user's own membership row) plus member_count and
    task_count for context.
    """
    valid_object_id(user_id)
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    projects = await Project.find({"members.user_id": user_id}).to_list()
    out: list[dict] = []
    for p in projects:
        # The user appears in members exactly once (members are a set
        # by user_id), so first match is fine.
        my_role = next(
            (m.role for m in p.members if m.user_id == user_id),
            None,
        )
        out.append({
            "id": str(p.id),
            "name": p.name,
            "color": p.color,
            "role": my_role,
            "member_count": len(p.members),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    # Newest first — matches the convention of the projects list page.
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


@router.get("/{user_id}/ai_runs")
async def list_user_ai_runs(
    user_id: str,
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(get_admin_user),
) -> dict:
    """Phase 6.B-3: recent MCP tool calls for a single user.

    Resolves the user's API keys and aggregates ``McpToolUsageBucket``
    over the last ``days`` days, grouped by tool name. We use the
    bucket store (not ``McpToolCallEvent``) because:
      * buckets retain 90 days vs events' 14 days,
      * buckets are pre-aggregated (cheap to scan),
      * the page wants "what the user has been doing" — counts per
        tool, not individual call traces.

    Returns: { total_calls, by_tool: [{tool_name, call_count}, ...],
               since: ISO8601 }
    """
    valid_object_id(user_id)
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Find this user's API keys. Same filter-in-Python approach as
    # _enrich_users_admin_meta — mongomock's DBRef path matching is
    # unreliable.
    user_id_str = str(user.id)
    all_keys = await McpApiKey.find_all().to_list()
    api_key_ids: list[str] = []
    for k in all_keys:
        owner_id = ""
        cb = k.created_by
        if hasattr(cb, "ref") and getattr(cb.ref, "id", None) is not None:
            owner_id = str(cb.ref.id)
        elif hasattr(cb, "id"):
            owner_id = str(cb.id)
        if owner_id == user_id_str:
            api_key_ids.append(str(k.id))

    since = datetime.now(UTC) - timedelta(days=days)
    if not api_key_ids:
        return {
            "total_calls": 0,
            "by_tool": [],
            "since": since.isoformat(),
            "days": days,
        }

    buckets = await McpToolUsageBucket.find(
        {
            "api_key_id": {"$in": api_key_ids},
            "hour": {"$gte": since},
        }
    ).to_list()

    # Aggregate by tool name in Python (mongomock-compatible).
    by_tool: dict[str, int] = {}
    total = 0
    for b in buckets:
        by_tool[b.tool_name] = by_tool.get(b.tool_name, 0) + b.call_count
        total += b.call_count

    rows = sorted(
        ({"tool_name": k, "call_count": v} for k, v in by_tool.items()),
        key=lambda r: r["call_count"],
        reverse=True,
    )[:limit]

    return {
        "total_calls": total,
        "by_tool": rows,
        "since": since.isoformat(),
        "days": days,
    }


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UpdateUserRequest, _: User = Depends(get_admin_user)) -> dict:
    valid_object_id(user_id)
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if body.name is not None:
        user.name = body.name
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    await user.save_updated()
    return _user_dict(user)


class ResetPasswordRequest(BaseModel):
    password: str | None = Field(None, min_length=8)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str, body: ResetPasswordRequest | None = None, _: User = Depends(get_admin_user)
) -> dict:
    """Reset a user's password. If password is provided, use it; otherwise generate a random one."""
    valid_object_id(user_id)
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.auth_type != AuthType.admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reset password for non-admin auth type users",
        )
    new_password = body.password if body and body.password else secrets.token_urlsafe(12)
    user.password_hash = hash_password(new_password)
    user.password_disabled = False
    await user.save_updated()
    return {"new_password": new_password}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, admin: User = Depends(get_admin_user)) -> None:
    """Permanently delete a user and clean up references.

    Cleanup order: references first, then the user document.
    If a cleanup step fails the user still exists, avoiding orphaned data.
    """
    valid_object_id(user_id)
    if user_id == str(admin.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user_ref = DBRef("users", user.id)

    # 1. Deactivate MCP API keys owned by this user (bulk update)
    await McpApiKey.find(
        {"created_by": user_ref, "is_active": True},
    ).update({"$set": {"is_active": False}})

    # 2. Remove user from project members
    projects = await Project.find({"members.user_id": user_id}).to_list()
    for project in projects:
        project.members = [m for m in project.members if m.user_id != user_id]
        await project.save()

    # 3. Nullify AllowedEmail.created_by references
    await AllowedEmail.find(
        {"created_by": user_ref},
    ).update({"$set": {"created_by": None}})

    # 4. Delete the user document last — if any step above fails,
    #    the user still exists and can be retried.
    await user.delete()


@router.get("/search/active")
async def search_active_users(
    q: str = Query("", min_length=0, max_length=100),
    limit: int = Query(20, ge=1, le=50),
    _: User = Depends(get_current_user),
) -> list[dict]:
    """Search active users by name or email. Available to all authenticated users."""
    import re

    filters: dict = {"is_active": True}
    if q.strip():
        pattern = re.escape(q.strip())
        filters["$or"] = [
            {"name": {"$regex": pattern, "$options": "i"}},
            {"email": {"$regex": pattern, "$options": "i"}},
        ]
    users = await User.find(filters).limit(limit).to_list()
    return [{"id": str(u.id), "name": u.name, "email": u.email, "picture_url": u.picture_url} for u in users]


# --- Allowed Emails ---

@router.get("/allowed-emails/", tags=["admin"])
async def list_allowed_emails(_: User = Depends(get_admin_user)) -> list[dict]:
    entries = await AllowedEmail.find_all().to_list()
    return [{"id": str(e.id), "email": e.email, "created_at": e.created_at.isoformat()} for e in entries]


@router.post("/allowed-emails/", status_code=status.HTTP_201_CREATED, tags=["admin"])
async def add_allowed_email(body: AllowedEmailRequest, admin: User = Depends(get_admin_user)) -> dict:
    existing = await AllowedEmail.find_one(AllowedEmail.email == body.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already allowed")
    entry = AllowedEmail(email=body.email, created_by=admin)
    await entry.insert()
    return {"id": str(entry.id), "email": entry.email, "created_at": entry.created_at.isoformat()}


@router.delete("/allowed-emails/{entry_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["admin"])
async def remove_allowed_email(entry_id: str, _: User = Depends(get_admin_user)) -> None:
    valid_object_id(entry_id)
    entry = await AllowedEmail.get(entry_id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await entry.delete()
