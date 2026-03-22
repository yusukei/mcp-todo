import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ....core.deps import get_admin_user
from ....core.validators import valid_object_id
from ....core.security import hash_api_key
from ....models import McpApiKey, User

router = APIRouter(prefix="/mcp-keys", tags=["mcp-keys"])


class CreateKeyRequest(BaseModel):
    name: str
    project_scopes: list[str] = []


@router.get("")
async def list_keys(_: User = Depends(get_admin_user)) -> list[dict]:
    keys = await McpApiKey.find(McpApiKey.is_active == True).to_list()
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "project_scopes": k.project_scopes,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_key(body: CreateKeyRequest, admin: User = Depends(get_admin_user)) -> dict:
    raw_key = f"mtodo_{secrets.token_hex(32)}"
    key = McpApiKey(
        key_hash=hash_api_key(raw_key),
        name=body.name,
        project_scopes=body.project_scopes,
        created_by=admin,
    )
    await key.insert()
    return {
        "id": str(key.id),
        "name": key.name,
        "key": raw_key,  # 発行時のみ返す
        "project_scopes": key.project_scopes,
        "created_at": key.created_at.isoformat(),
    }


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, _: User = Depends(get_admin_user)) -> None:
    valid_object_id(key_id)
    key = await McpApiKey.get(key_id)
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    key.is_active = False
    await key.save()
