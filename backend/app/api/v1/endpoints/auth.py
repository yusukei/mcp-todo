import asyncio
import logging
import secrets

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuthError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from ....core.config import settings
from ....core.deps import get_current_user
from ....core.redis import get_redis
from ....core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    verify_password,
)
from ....models import AllowedEmail, User
from ....models.user import AuthType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_STATE_TTL = 600  # 10分
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 900  # 15分
_REFRESH_JTI_TTL = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400  # seconds


async def _store_refresh_jti(jti: str) -> None:
    """Store a refresh token JTI in Redis so it can be validated later."""
    redis = get_redis()
    await redis.set(f"refresh_jti:{jti}", "valid", ex=_REFRESH_JTI_TTL)


async def _validate_and_revoke_jti(jti: str | None) -> bool:
    """Validate a JTI exists in Redis and delete it (one-time use).

    Returns True if valid (or if jti is None for backward compatibility).
    """
    if jti is None:
        # Backward compatibility: old tokens without JTI are accepted
        return True
    redis = get_redis()
    result = await redis.delete(f"refresh_jti:{jti}")
    return result > 0


async def _create_and_store_refresh_token(subject: str) -> str:
    """Create a refresh token and store its JTI in Redis."""
    token, jti = create_refresh_token(subject)
    await _store_refresh_jti(jti)
    return token


async def _check_rate_limit(email: str) -> None:
    redis = get_redis()
    key = f"login_attempts:{email}"
    attempts = await redis.get(key)
    if attempts and int(attempts) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )


async def _record_failed_login(email: str) -> None:
    redis = get_redis()
    key = f"login_attempts:{email}"
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, _LOGIN_LOCKOUT_SECONDS)
    await pipe.execute()


async def _clear_login_attempts(email: str) -> None:
    redis = get_redis()
    await redis.delete(f"login_attempts:{email}")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    await _check_rate_limit(body.username)
    user = await User.find_one(User.email == body.username, User.auth_type == AuthType.admin)
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        await _record_failed_login(body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    await _clear_login_attempts(body.username)
    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=await _create_and_store_refresh_token(str(user.id)),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest) -> TokenResponse:
    payload = decode_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # Validate and revoke old JTI (one-time use)
    jti = payload.get("jti")
    if not await _validate_and_revoke_jti(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token already used")

    user = await User.get(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=await _create_and_store_refresh_token(str(user.id)),
    )


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "is_admin": user.is_admin,
        "picture_url": user.picture_url,
    }


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
    return RedirectResponse(url)


@router.get("/google/callback", response_model=TokenResponse)
async def google_callback(code: str, state: str) -> TokenResponse:
    redis = get_redis()
    key = f"oauth:state:{state}"
    if not await redis.get(key):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid state")
    await redis.delete(key)

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

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=await _create_and_store_refresh_token(str(user.id)),
    )
