"""MCP tools for project-scoped encrypted secret management."""

import logging
import re

from fastmcp.exceptions import ToolError

from ...core.crypto import decrypt, encrypt
from ...models.secret import ProjectSecret, SecretAccessLog
from ...services.serializers import secret_to_dict as _secret_dict
from ..auth import authenticate, check_project_access
from ..server import mcp
from .projects import _resolve_project_id

logger = logging.getLogger(__name__)

# Key name validation: env-var style (letters, digits, underscores).
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_KEY_LEN = 255
_MAX_VALUE_LEN = 10_000
_MAX_DESC_LEN = 1_000


async def _check_owner(project_id: str, key_info: dict):
    """Require owner role (or admin) for write operations."""
    project = await check_project_access(project_id, key_info)
    if not key_info.get("is_admin") and not project.is_owner(key_info["user_id"]):
        raise ToolError("Only project owners can modify secrets")
    return project


async def _log_access(
    project_id: str, secret_key: str, operation: str, key_info: dict,
) -> None:
    """Record a secret access event to the audit log."""
    log = SecretAccessLog(
        project_id=project_id,
        secret_key=secret_key,
        operation=operation,
        user_id=key_info.get("user_id", ""),
        auth_kind=key_info.get("auth_kind", ""),
    )
    await log.insert()


def _validate_key(key: str) -> str:
    if not key or not key.strip():
        raise ToolError("Secret key is required")
    key = key.strip()
    if len(key) > _MAX_KEY_LEN:
        raise ToolError(f"Key exceeds maximum length of {_MAX_KEY_LEN} characters")
    if not _KEY_RE.match(key):
        raise ToolError(
            "Key must be a valid environment variable name "
            "(letters, digits, underscores; must start with a letter or underscore)"
        )
    return key


@mcp.tool()
async def list_secrets(
    project_id: str,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List secrets in a project. Returns key names and descriptions only — values are never included.

    Args:
        project_id: Project ID or project name
        limit: Maximum number of results (default 50, max 200)
        skip: Number of results to skip for pagination
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    limit = min(max(1, limit), 200)
    skip = max(0, skip)

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


@mcp.tool()
async def set_secret(
    project_id: str,
    key: str,
    value: str,
    description: str | None = None,
) -> dict:
    """Create or update a project secret. Only project owners can use this tool.

    If a secret with the same key already exists in the project, its value
    and description are updated (upsert). The value is encrypted at rest.

    Args:
        project_id: Project ID or project name
        key: Secret key name (env-var style: letters, digits, underscores)
        value: Secret value to encrypt and store (max 10000 chars)
        description: Optional human-readable description of the secret's purpose
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await _check_owner(project_id, key_info)

    key = _validate_key(key)

    if not value:
        raise ToolError("Secret value must not be empty")
    if len(value) > _MAX_VALUE_LEN:
        raise ToolError(f"Value exceeds maximum length of {_MAX_VALUE_LEN} characters")
    if description and len(description) > _MAX_DESC_LEN:
        raise ToolError(f"Description exceeds maximum length of {_MAX_DESC_LEN} characters")

    creator = f"mcp:{key_info['key_name']}" if key_info.get("key_name") else f"user:{key_info.get('user_id', 'unknown')}"
    encrypted = encrypt(value)

    existing = await ProjectSecret.find_one(
        ProjectSecret.project_id == project_id,
        ProjectSecret.key == key,
    )

    if existing:
        existing.encrypted_value = encrypted
        if description is not None:
            existing.description = description
        existing.updated_by = creator
        await existing.save_updated()
        await _log_access(project_id, key, "set", key_info)
        return {**_secret_dict(existing), "_action": "updated"}
    else:
        secret = ProjectSecret(
            project_id=project_id,
            key=key,
            encrypted_value=encrypted,
            description=description or "",
            created_by=creator,
            updated_by=creator,
        )
        await secret.insert()
        await _log_access(project_id, key, "set", key_info)
        return {**_secret_dict(secret), "_action": "created"}


@mcp.tool()
async def get_secret(
    project_id: str,
    key: str,
) -> dict:
    """Get the decrypted value of a project secret.

    WARNING: The returned value will be visible in the LLM context.
    For safer usage, prefer ``inject_secrets=True`` on ``remote_exec``
    which injects secrets as environment variables without exposing
    them in the conversation.

    Args:
        project_id: Project ID or project name
        key: Secret key name
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await check_project_access(project_id, key_info)

    key = _validate_key(key)
    secret = await ProjectSecret.find_one(
        ProjectSecret.project_id == project_id,
        ProjectSecret.key == key,
    )
    if not secret:
        raise ToolError(f"Secret not found: {key}")

    await _log_access(project_id, key, "get", key_info)

    decrypted = decrypt(secret.encrypted_value)
    return {
        "key": key,
        "value": decrypted,
        "description": secret.description,
        "_warning": (
            "This secret value is now in the LLM context. "
            "Prefer inject_secrets on remote_exec for safer usage."
        ),
    }


@mcp.tool()
async def delete_secret(
    project_id: str,
    key: str,
) -> dict:
    """Delete a project secret. Only project owners can use this tool.

    Args:
        project_id: Project ID or project name
        key: Secret key name to delete
    """
    key_info = await authenticate()
    project_id = await _resolve_project_id(project_id)
    await _check_owner(project_id, key_info)

    key = _validate_key(key)
    secret = await ProjectSecret.find_one(
        ProjectSecret.project_id == project_id,
        ProjectSecret.key == key,
    )
    if not secret:
        raise ToolError(f"Secret not found: {key}")

    await secret.delete()
    await _log_access(project_id, key, "delete", key_info)

    return {"success": True, "key": key, "project_id": project_id}
