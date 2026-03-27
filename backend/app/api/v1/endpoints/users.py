import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import DuplicateKeyError

from ....core.deps import get_admin_user, get_current_user
from ....core.validators import valid_object_id
from ....core.security import hash_password
from ....models import AllowedEmail, User
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


def _user_dict(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "auth_type": user.auth_type,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "picture_url": user.picture_url,
        "created_at": user.created_at.isoformat(),
    }


@router.get("")
async def list_users(
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    _: User = Depends(get_admin_user),
) -> dict:
    query = User.find_all()
    total = await query.count()
    users = await query.skip(skip).limit(limit).to_list()
    return {"items": [_user_dict(u) for u in users], "total": total, "limit": limit, "skip": skip}


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
    return _user_dict(user)


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
    valid_object_id(user_id)
    if user_id == str(admin.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = False
    await user.save_updated()


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
