import asyncio
import base64
import json
import logging
import secrets

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    options_to_json,
    parse_authentication_credential_json,
    parse_registration_credential_json,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ....core.config import settings
from ....core.deps import get_current_user
from ....core.redis import get_redis
from ....core.security import (
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    set_auth_cookies,
    verify_password,
)
from ....models import AllowedEmail, User
from ....models.user import AuthType, WebAuthnCredential

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
async def login(body: LoginRequest, response: Response) -> TokenResponse:
    await _check_rate_limit(body.username)
    user = await User.find_one(User.email == body.username, User.auth_type == AuthType.admin)
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        await _record_failed_login(body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    if user.password_disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password login is disabled. Use passkey instead.",
        )

    await _clear_login_attempts(body.username)
    access_token = create_access_token(str(user.id))
    refresh_token = await _create_and_store_refresh_token(str(user.id))
    set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, response: Response) -> TokenResponse:
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

    access_token = create_access_token(str(user.id))
    refresh_token = await _create_and_store_refresh_token(str(user.id))
    set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "is_admin": user.is_admin,
        "picture_url": user.picture_url,
        "auth_type": user.auth_type,
        "has_passkeys": len(user.webauthn_credentials) > 0,
        "password_disabled": user.password_disabled,
    }


@router.post("/logout")
async def logout(response: Response, _: User = Depends(get_current_user)) -> dict:
    clear_auth_cookies(response)
    return {"detail": "Logged out"}


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
async def google_callback(code: str, state: str, response: Response) -> TokenResponse:
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

    access_token = create_access_token(str(user.id))
    refresh_token = await _create_and_store_refresh_token(str(user.id))
    set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


# ---------------------------------------------------------------------------
# WebAuthn / Passkey
# ---------------------------------------------------------------------------

_WEBAUTHN_CHALLENGE_TTL = 300  # 5 minutes


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


class WebAuthnCredentialResponse(BaseModel):
    credential_id: str
    name: str
    created_at: str


class WebAuthnRegisterVerifyRequest(BaseModel):
    credential: dict
    name: str = ""


class WebAuthnAuthenticateVerifyRequest(BaseModel):
    credential: dict


class WebAuthnAuthenticateOptionsRequest(BaseModel):
    email: str = ""


@router.post("/webauthn/register/options")
async def webauthn_register_options(user: User = Depends(get_current_user)) -> dict:
    """Generate registration options for the current user (admin only)."""
    if user.auth_type != AuthType.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Passkey is only available for local users")

    exclude_credentials = [
        PublicKeyCredentialDescriptor(id=_b64url_decode(c.credential_id))
        for c in user.webauthn_credentials
    ]

    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=str(user.id).encode(),
        user_name=user.email,
        user_display_name=user.name,
        exclude_credentials=exclude_credentials,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )

    # Store challenge in Redis
    redis = get_redis()
    challenge_b64 = _b64url_encode(options.challenge)
    await redis.set(
        f"webauthn:reg:{user.id}",
        challenge_b64,
        ex=_WEBAUTHN_CHALLENGE_TTL,
    )

    options_dict = json.loads(options_to_json(options))
    return options_dict


@router.post("/webauthn/register/verify")
async def webauthn_register_verify(
    body: WebAuthnRegisterVerifyRequest,
    user: User = Depends(get_current_user),
) -> WebAuthnCredentialResponse:
    """Verify registration and store the new credential."""
    if user.auth_type != AuthType.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Passkey is only available for local users")

    redis = get_redis()
    challenge_b64 = await redis.get(f"webauthn:reg:{user.id}")
    if not challenge_b64:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration challenge expired")
    await redis.delete(f"webauthn:reg:{user.id}")

    expected_challenge = _b64url_decode(challenge_b64)

    try:
        credential = parse_registration_credential_json(json.dumps(body.credential))
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
        )
    except Exception as e:
        logger.warning("WebAuthn registration verification failed: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification failed")

    new_credential = WebAuthnCredential(
        credential_id=_b64url_encode(verification.credential_id),
        public_key=_b64url_encode(verification.credential_public_key),
        sign_count=verification.sign_count,
        name=body.name or f"Passkey {len(user.webauthn_credentials) + 1}",
    )

    user.webauthn_credentials.append(new_credential)
    await user.save_updated()

    return WebAuthnCredentialResponse(
        credential_id=new_credential.credential_id,
        name=new_credential.name,
        created_at=new_credential.created_at.isoformat(),
    )


@router.post("/webauthn/authenticate/options")
async def webauthn_authenticate_options(body: WebAuthnAuthenticateOptionsRequest) -> dict:
    """Generate authentication options. Optionally filter by email."""
    allow_credentials = []

    if body.email:
        user = await User.find_one(User.email == body.email, User.auth_type == AuthType.admin)
        if user and user.webauthn_credentials:
            allow_credentials = [
                PublicKeyCredentialDescriptor(
                    id=_b64url_decode(c.credential_id),
                    transports=c.transports if c.transports else None,
                )
                for c in user.webauthn_credentials
            ]

    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        allow_credentials=allow_credentials if allow_credentials else None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    redis = get_redis()
    challenge_b64 = _b64url_encode(options.challenge)
    await redis.set(
        f"webauthn:auth:{challenge_b64}",
        "1",
        ex=_WEBAUTHN_CHALLENGE_TTL,
    )

    options_dict = json.loads(options_to_json(options))
    return options_dict


@router.post("/webauthn/authenticate/verify", response_model=TokenResponse)
async def webauthn_authenticate_verify(body: WebAuthnAuthenticateVerifyRequest, response: Response) -> TokenResponse:
    """Verify authentication assertion and return JWT tokens."""
    try:
        credential = parse_authentication_credential_json(json.dumps(body.credential))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid credential format")

    credential_id_b64 = _b64url_encode(credential.raw_id)

    # Find user with this credential
    user = await User.find_one(
        {"webauthn_credentials.credential_id": credential_id_b64, "auth_type": AuthType.admin}
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown credential")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    # Find the matching credential
    stored_cred = next(
        (c for c in user.webauthn_credentials if c.credential_id == credential_id_b64), None
    )
    if not stored_cred:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown credential")

    # Retrieve and validate challenge
    redis = get_redis()
    try:
        client_data = json.loads(credential.response.client_data_json)
        challenge_b64 = client_data["challenge"]
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid client data")

    stored = await redis.get(f"webauthn:auth:{challenge_b64}")
    if not stored:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication challenge expired")
    await redis.delete(f"webauthn:auth:{challenge_b64}")

    expected_challenge = _b64url_decode(challenge_b64)

    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=_b64url_decode(stored_cred.public_key),
            credential_current_sign_count=stored_cred.sign_count,
        )
    except Exception as e:
        logger.warning("WebAuthn authentication verification failed: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Verification failed")

    # Update sign count
    stored_cred.sign_count = verification.new_sign_count
    await user.save_updated()

    access_token = create_access_token(str(user.id))
    refresh_token = await _create_and_store_refresh_token(str(user.id))
    set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.get("/webauthn/credentials")
async def webauthn_list_credentials(
    user: User = Depends(get_current_user),
) -> list[WebAuthnCredentialResponse]:
    """List the current user's registered passkeys."""
    return [
        WebAuthnCredentialResponse(
            credential_id=c.credential_id,
            name=c.name,
            created_at=c.created_at.isoformat(),
        )
        for c in user.webauthn_credentials
    ]


@router.delete("/webauthn/credentials/{credential_id}")
async def webauthn_delete_credential(
    credential_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Delete a registered passkey."""
    original_count = len(user.webauthn_credentials)
    new_credentials = [
        c for c in user.webauthn_credentials if c.credential_id != credential_id
    ]
    if len(new_credentials) == original_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    if len(new_credentials) == 0 and user.password_disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete last passkey while password login is disabled",
        )
    user.webauthn_credentials = new_credentials
    await user.save_updated()
    return {"ok": True}


class PasswordDisabledRequest(BaseModel):
    disabled: bool


@router.patch("/webauthn/password-disabled")
async def webauthn_toggle_password(
    body: PasswordDisabledRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Enable or disable password login. Requires at least one passkey to disable."""
    if user.auth_type != AuthType.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only available for local users")
    if body.disabled and not user.webauthn_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Register at least one passkey before disabling password",
        )
    user.password_disabled = body.disabled
    await user.save_updated()
    return {"password_disabled": user.password_disabled}
