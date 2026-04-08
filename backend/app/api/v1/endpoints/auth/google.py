"""Google OAuth 2.0 login flow."""
from __future__ import annotations

import asyncio
import logging
import secrets

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuthError
from fastapi import APIRouter, Cookie, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pymongo.errors import DuplicateKeyError

from .....core.config import settings
from .....core.redis import get_redis
from .....core.security import create_access_token, set_auth_cookies
from .....models import AllowedEmail, User
from .....models.user import AuthType
from ._shared import TokenResponse, _OAUTH_STATE_TTL, _create_and_store_refresh_token

logger = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# State double-submit cookie. The /google route plants this cookie on
# the browser that initiated the flow; /google/callback rejects the
# request unless the cookie matches the `state` query parameter. This
# closes a "login CSRF" hole where an attacker could start an OAuth
# flow against their own Google account, then trick a victim into
# completing it (the victim would end up logged in as the attacker).
_OAUTH_STATE_COOKIE = "oauth_state"


def _set_oauth_state_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        key=_OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        # Lax is required: Google bounces the user back via a top-level
        # navigation, and Strict would strip the cookie on that hop.
        samesite="lax",
        path="/api/v1/auth/google",
        max_age=_OAUTH_STATE_TTL,
        domain=settings.COOKIE_DOMAIN or None,
    )


def _clear_oauth_state_cookie(response: Response) -> None:
    response.delete_cookie(
        _OAUTH_STATE_COOKIE,
        path="/api/v1/auth/google",
        domain=settings.COOKIE_DOMAIN or None,
    )


@router.get("/google")
async def google_login() -> RedirectResponse:
    state = secrets.token_urlsafe(16)
    redis = get_redis()
    await redis.set(f"oauth:state:{state}", "1", ex=_OAUTH_STATE_TTL)

    client = AsyncOAuth2Client(
        client_id=settings.GOOGLE_CLIENT_ID,
        redirect_uri=f"{settings.FRONTEND_URL}/auth/google/callback",
        scope="openid email profile",
    )
    url, _ = client.create_authorization_url(GOOGLE_AUTH_URL, state=state)
    redirect = RedirectResponse(url)
    _set_oauth_state_cookie(redirect, state)
    return redirect


@router.get("/google/callback", response_model=TokenResponse)
async def google_callback(
    code: str,
    state: str,
    response: Response,
    oauth_state: str | None = Cookie(default=None, alias=_OAUTH_STATE_COOKIE),
) -> TokenResponse:
    # Double-submit: the state in the URL must match the state in the
    # cookie that we planted on this very browser when the flow started.
    # secrets.compare_digest avoids leaking timing information.
    if not oauth_state or not secrets.compare_digest(oauth_state, state):
        _clear_oauth_state_cookie(response)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")

    redis = get_redis()
    key = f"oauth:state:{state}"
    if not await redis.get(key):
        _clear_oauth_state_cookie(response)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")
    await redis.delete(key)
    _clear_oauth_state_cookie(response)

    async with AsyncOAuth2Client(
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        redirect_uri=f"{settings.FRONTEND_URL}/auth/google/callback",
    ) as client:
        try:
            token = await client.fetch_token(GOOGLE_TOKEN_URL, code=code)
        except (httpx.HTTPError, OAuthError, asyncio.TimeoutError, KeyError, ValueError) as exc:
            logger.exception("Failed to fetch token from Google: %s", exc)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google authentication failed")
        try:
            resp = await client.get(GOOGLE_USERINFO_URL, token=token)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.exception("Failed to fetch user info from Google: %s", exc)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch user info from Google")
        info = resp.json()

    email: str = info.get("email", "")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No email from Google")

    allowed = await AllowedEmail.find_one(AllowedEmail.email == email)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not allowed")

    user = await User.find_one(User.email == email)
    if not user:
        user = User(
            email=email,
            name=info.get("name", email),
            auth_type=AuthType.google,
            google_id=info.get("sub"),
            picture_url=info.get("picture"),
        )
        try:
            await user.insert()
        except DuplicateKeyError:
            user = await User.find_one(User.email == email)
            if not user:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    else:
        user.name = info.get("name", user.name)
        user.picture_url = info.get("picture", user.picture_url)
        user.google_id = info.get("sub", user.google_id)
        await user.save_updated()

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    access_token = create_access_token(str(user.id))
    refresh_token = await _create_and_store_refresh_token(str(user.id))
    set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)
