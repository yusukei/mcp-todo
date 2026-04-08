import secrets

from bson import DBRef
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....core.security import hash_api_key
from ....models import McpApiKey, User

router = APIRouter(prefix="/mcp-keys", tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    name: str


def _key_dict(k: McpApiKey) -> dict:
    return {
        "id": str(k.id),
        "name": k.name,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "created_at": k.created_at.isoformat(),
    }


@router.get("")
async def list_my_keys(user: User = Depends(get_current_user)) -> list[dict]:
    """List API keys owned by the current user."""
    keys = await McpApiKey.find(
        {"created_by": DBRef("users", user.id), "is_active": True}
    ).to_list()
    return [_key_dict(k) for k in keys]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_my_key(body: CreateKeyRequest, user: User = Depends(get_current_user)) -> dict:
    """Create an API key owned by the current user."""
    raw_key = f"mtodo_{secrets.token_hex(32)}"
    key = McpApiKey(
        key_hash=hash_api_key(raw_key),
        name=body.name,
        created_by=user,
    )
    await key.insert()
    return {
        **_key_dict(key),
        "key": raw_key,  # returned only at creation
    }


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_key(key_id: str, user: User = Depends(get_current_user)) -> None:
    """Revoke an API key owned by the current user."""
    valid_object_id(key_id)
    key = await McpApiKey.get(key_id)
    if not key or not key.created_by or key.created_by.ref.id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    key.is_active = False
    await key.save()
