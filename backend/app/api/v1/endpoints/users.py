import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ....core.deps import get_admin_user, get_current_user
from ....core.security import hash_password
from ....models import AllowedEmail, User
from ....models.user import AuthType

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserRequest(BaseModel):
    email: str
    name: str
    password: str | None = None
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class AllowedEmailRequest(BaseModel):
    email: str


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
async def list_users(_: User = Depends(get_admin_user)) -> list[dict]:
    users = await User.find_all().to_list()
    return [_user_dict(u) for u in users]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(body: CreateUserRequest, admin: User = Depends(get_admin_user)) -> dict:
    existing = await User.find_one(User.email == body.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    password_hash = hash_password(body.password) if body.password else hash_password(secrets.token_hex(16))
    user = User(
        email=body.email,
        name=body.name,
        auth_type=AuthType.admin,
        password_hash=password_hash,
        is_admin=body.is_admin,
    )
    await user.insert()
    return _user_dict(user)


@router.get("/{user_id}")
async def get_user(user_id: str, _: User = Depends(get_admin_user)) -> dict:
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _user_dict(user)


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UpdateUserRequest, _: User = Depends(get_admin_user)) -> dict:
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


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, admin: User = Depends(get_admin_user)) -> None:
    if user_id == str(admin.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = False
    await user.save_updated()


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
    entry = await AllowedEmail.get(entry_id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await entry.delete()
