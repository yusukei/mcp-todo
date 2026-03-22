import logging
import secrets

from authlib.integrations.httpx_client import AsyncOAuth2Client
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
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest) -> TokenResponse:
    payload = decode_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = await User.get(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
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
        except Exception:
            logger.exception("Failed to fetch token from Google")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google authentication failed")
        try:
            resp = await client.get(GOOGLE_USERINFO_URL, token=token)
        except Exception:
            logger.exception("Failed to fetch user info from Google")
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
        refresh_token=create_refresh_token(str(user.id)),
    )
