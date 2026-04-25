"""Provision an ErrorTrackingConfig for a newly-created Project.

Called automatically from both the REST API and MCP create_project
handlers so that every Project has error tracking ready from the moment
it is created.
"""

from __future__ import annotations

import secrets as _secrets

from ...models.error_tracker import DsnKeyRecord, ErrorAuditLog, ErrorTrackingConfig


def _generate_dsn_pair() -> tuple[str, str, str]:
    """Return ``(public_key, secret_key, secret_key_hash)``."""
    from argon2 import PasswordHasher

    public_key = _secrets.token_hex(16)  # 32 hex chars
    secret_key = _secrets.token_urlsafe(32)
    hasher = PasswordHasher()
    return public_key, secret_key, hasher.hash(secret_key)


async def provision_error_tracking_config(
    project_id: str,
    project_name: str,
    created_by: str = "system",
) -> ErrorTrackingConfig:
    """Create and persist an ErrorTrackingConfig for *project_id*.

    Safe to call even if one already exists — returns the existing
    config without overwriting it (idempotent).
    """
    existing = await ErrorTrackingConfig.find_one(
        ErrorTrackingConfig.project_id == project_id
    )
    if existing is not None:
        return existing

    public_key, _secret_key, secret_hash = _generate_dsn_pair()
    config = ErrorTrackingConfig(
        project_id=project_id,
        name=project_name,
        keys=[
            DsnKeyRecord(
                public_key=public_key,
                secret_key_hash=secret_hash,
                secret_key_prefix=_secret_key[:8],
            )
        ],
        allowed_origin_wildcard=True,
        allowed_origins=[],
        created_by=created_by,
    )
    # Assign the numeric DSN id BEFORE insert so the row is fully
    # usable from the moment the project exists — the Sentry SDK
    # would otherwise truncate the ObjectId hex in the DSN path
    # to its leading digit run and 401 every event.
    config.ensure_numeric_dsn_id()
    await config.insert()
    await ErrorAuditLog(
        project_id=project_id,
        error_project_id=str(config.id),
        action="create_project",
        actor_id=created_by,
        actor_kind="system",
        details={"auto_provisioned": True},
    ).insert()
    return config


__all__ = ["provision_error_tracking_config"]
