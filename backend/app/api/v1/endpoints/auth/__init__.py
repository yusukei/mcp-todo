"""Auth endpoints package.

Splits the former 548-line ``auth.py`` into 4 submodules by auth scheme:
- ``_shared``  — rate limiting, refresh-token JTI helpers, TokenResponse
- ``jwt``      — /login, /refresh, /me, /logout
- ``google``   — /google, /google/callback (OAuth 2.0)
- ``webauthn`` — /webauthn/* (passkey registration, authentication, management)

Tests that need the underscore-prefixed helpers (e.g. ``_check_rate_limit``,
``_LOGIN_MAX_ATTEMPTS``) should import them directly from
``app.api.v1.endpoints.auth._shared`` rather than relying on a
package-level re-export.
"""
from __future__ import annotations

from fastapi import APIRouter

from .google import router as _google_router
from .jwt import router as _jwt_router
from .webauthn import router as _webauthn_router

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(_jwt_router)
router.include_router(_google_router)
router.include_router(_webauthn_router)

__all__ = ["router"]
