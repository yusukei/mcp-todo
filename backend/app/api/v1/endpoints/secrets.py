"""REST API endpoints for project-scoped secret management."""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ....core.crypto import decrypt, encrypt
from ....core.deps import get_current_user
from ....core.validators import valid_object_id
from ....models import Project, User
from ....models.secret import ProjectSecret, SecretAccessLog
from ....services.serializers import secret_to_dict as _secret_dict

router = APIRouter(prefix="/projects/{project_id}/secrets", tags=["secrets"])

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_KEY_LEN = 255
_MAX_VALUE_LEN = 10_000
_MAX_DESC_LEN = 1_000


# ── Request schemas ──────────────────────────────────────────


class CreateSecretRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=_MAX_KEY_LEN)
    value: str = Field(..., min_length=1, max_length=_MAX_VALUE_LEN)
    description: str = Field("", max_length=_MAX_DESC_LEN)


class UpdateSecretRequest(BaseModel):
    value: str | None = Field(None, min_length=1, max_length=_MAX_VALUE_LEN)
    description: str | None = Field(None, max_length=_MAX_DESC_LEN)


# ── Helpers ──────────────────────────────────────────────────


async def _check_project_access(project_id: str, user: User) -> Project:
    """Return project if user is admin or member; raise 403 otherwise."""
    from ....models.project import ProjectStatus as _ProjectStatus

    valid_object_id(project_id)
    project = await Project.get(project_id)
    if not project or project.status == _ProjectStatus.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not user.is_admin and not project.has_member(str(user.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")
    return project


def _check_owner(project: Project, user: User) -> None:
    """Raise 403 if user is not project owner or admin."""
    if user.is_admin:
        return
    if not project.is_owner(str(user.id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners can modify secrets",
        )


def _validate_key(key: str) -> str:
    key = key.strip()
    if not _KEY_RE.match(key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Key must be a valid env var name (letters, digits, underscores)",
        )
    return key


async def _log_access(
    project_id: str, secret_key: str, operation: str, user: User,
) -> None:
    await SecretAccessLog(
        project_id=project_id,
        secret_key=secret_key,
        operation=operation,
        user_id=str(user.id),
        auth_kind="oauth",
    ).insert()


# ── Endpoints ────────────────────────────────────────────────


@router.get("/")
async def list_secrets(
    project_id: str,
    limit: int = Query(50, ge=1),
    skip: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
):
    """List secrets in a project. Values are never included."""
    await _check_project_access(project_id, user)

    total = await ProjectSecret.find(
        ProjectSecret.project_id == project_id,
    ).count()
    secrets = await (
        ProjectSecret.find(ProjectSecret.project_id == project_id)
        .skip(skip)
        .limit(limit)
        .sort("-updated_at")
        .to_list()
    )
    return {
        "items": [_secret_dict(s) for s in secrets],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_secret(
    project_id: str,
    body: CreateSecretRequest,
    user: User = Depends(get_current_user),
):
    """Create a new secret. Owner only."""
    project = await _check_project_access(project_id, user)
    _check_owner(project, user)

    key = _validate_key(body.key)

    existing = await ProjectSecret.find_one(
        ProjectSecret.project_id == project_id,
        ProjectSecret.key == key,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Secret with key '{key}' already exists. Use PUT to update.",
        )

    creator = f"user:{user.id}"
    secret = ProjectSecret(
        project_id=project_id,
        key=key,
        encrypted_value=encrypt(body.value),
        description=body.description,
        created_by=creator,
        updated_by=creator,
    )
    await secret.insert()
    await _log_access(project_id, key, "set", user)
    return _secret_dict(secret)


@router.put("/{key}")
async def update_secret(
    project_id: str,
    key: str,
    body: UpdateSecretRequest,
    user: User = Depends(get_current_user),
):
    """Update an existing secret. Owner only."""
    project = await _check_project_access(project_id, user)
    _check_owner(project, user)

    key = _validate_key(key)
    secret = await ProjectSecret.find_one(
        ProjectSecret.project_id == project_id,
        ProjectSecret.key == key,
    )
    if not secret:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Secret not found: {key}")

    if body.value is not None:
        secret.encrypted_value = encrypt(body.value)
    if body.description is not None:
        secret.description = body.description
    secret.updated_by = f"user:{user.id}"
    await secret.save_updated()
    await _log_access(project_id, key, "set", user)
    return _secret_dict(secret)


@router.delete("/{key}", status_code=status.HTTP_200_OK)
async def delete_secret(
    project_id: str,
    key: str,
    user: User = Depends(get_current_user),
):
    """Delete a secret. Owner only."""
    project = await _check_project_access(project_id, user)
    _check_owner(project, user)

    key = _validate_key(key)
    secret = await ProjectSecret.find_one(
        ProjectSecret.project_id == project_id,
        ProjectSecret.key == key,
    )
    if not secret:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Secret not found: {key}")

    await secret.delete()
    await _log_access(project_id, key, "delete", user)
    return {"success": True, "key": key}


@router.get("/{key}/value")
async def get_secret_value(
    project_id: str,
    key: str,
    user: User = Depends(get_current_user),
):
    """Get the decrypted value of a secret. Member access, audited."""
    await _check_project_access(project_id, user)

    key = _validate_key(key)
    secret = await ProjectSecret.find_one(
        ProjectSecret.project_id == project_id,
        ProjectSecret.key == key,
    )
    if not secret:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Secret not found: {key}")

    await _log_access(project_id, key, "get", user)
    return {
        "key": key,
        "value": decrypt(secret.encrypted_value),
    }
