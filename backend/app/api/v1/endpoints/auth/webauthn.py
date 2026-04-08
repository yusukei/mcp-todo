"""WebAuthn / Passkey registration + authentication + credential management."""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
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

from .....core.config import settings
from .....core.deps import get_current_user
from .....core.redis import get_redis
from .....core.security import create_access_token, set_auth_cookies
from .....models import User
from .....models.user import AuthType, WebAuthnCredential
from ._shared import TokenResponse, _create_and_store_refresh_token

logger = logging.getLogger(__name__)

router = APIRouter()

_WEBAUTHN_CHALLENGE_TTL = 300  # 5 minutes


def _require_local_user(user: User) -> None:
    """Reject WebAuthn API access from non-local (Google etc.) users.

    Passkeys are only meaningful for the password/admin auth path; a
    Google-authenticated user would be relying on Google's own MFA.
    """
    if user.auth_type != AuthType.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Passkey is only available for local users",
        )


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


# `credential` is the SimpleWebAuthn-shaped registration / authentication
# response from the browser. We treat it as opaque JSON here and let
# `parse_*_credential_json` validate the structure inside the handler;
# this keeps Pydantic from rejecting valid SimpleWebAuthn payloads
# whose schema we do not own.
class WebAuthnRegisterVerifyRequest(BaseModel):
    credential: dict[str, Any]
    name: str = ""


class WebAuthnAuthenticateVerifyRequest(BaseModel):
    credential: dict[str, Any]


class WebAuthnAuthenticateOptionsRequest(BaseModel):
    email: str = ""


class PasswordDisabledRequest(BaseModel):
    disabled: bool


@router.post("/webauthn/register/options")
async def webauthn_register_options(user: User = Depends(get_current_user)) -> dict:
    """Generate registration options for the current user (admin only)."""
    _require_local_user(user)

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
    _require_local_user(user)

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
    except Exception:
        # Boundary catch: the webauthn library raises a half-dozen
        # different exception types (InvalidRegistrationResponse,
        # InvalidJSONStructure, etc.) and we need to translate every
        # one into a 400. Log the full traceback so the cause is
        # actually debuggable instead of swallowing it.
        logger.exception("WebAuthn registration verification failed")
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
    expected_user_id: str | None = None

    if body.email:
        user = await User.find_one(User.email == body.email, User.auth_type == AuthType.admin)
        if user and user.webauthn_credentials:
            expected_user_id = str(user.id)
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

    # Store the challenge keyed by its own b64 value, with the expected
    # user id (if any) as the value. /verify enforces this binding so a
    # challenge issued for user A cannot be consumed by user B's
    # credential — which the WebAuthn spec requires but the previous
    # `"1"` placeholder did not enforce.
    redis = get_redis()
    challenge_b64 = _b64url_encode(options.challenge)
    await redis.set(
        f"webauthn:auth:{challenge_b64}",
        expected_user_id or "*",  # "*" = discoverable / no email pre-filter
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
        logger.exception("WebAuthn authenticate: failed to parse credential")
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
    except (json.JSONDecodeError, KeyError, AttributeError):
        logger.exception("WebAuthn authenticate: malformed client_data_json")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid client data")

    stored = await redis.get(f"webauthn:auth:{challenge_b64}")
    if not stored:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Authentication challenge expired")
    await redis.delete(f"webauthn:auth:{challenge_b64}")

    # Enforce the user binding established at /options time. If the
    # challenge was scoped to a specific user (email pre-filter) then
    # only that user's credential may consume it. "*" means
    # discoverable mode where any registered passkey is acceptable.
    expected_user_id = stored.decode() if isinstance(stored, bytes) else stored
    if expected_user_id != "*" and expected_user_id != str(user.id):
        logger.warning(
            "WebAuthn challenge user mismatch: expected=%s actual=%s",
            expected_user_id, user.id,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Challenge user mismatch")

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
    except Exception:
        # Boundary catch for the same reason as register/verify above —
        # the webauthn library raises many distinct types and we collapse
        # them into a 401. Use logger.exception so the original cause is
        # actually visible.
        logger.exception("WebAuthn authentication verification failed")
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


@router.patch("/webauthn/password-disabled")
async def webauthn_toggle_password(
    body: PasswordDisabledRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Enable or disable password login. Requires at least one passkey to disable."""
    _require_local_user(user)
    if body.disabled and not user.webauthn_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Register at least one passkey before disabling password",
        )
    user.password_disabled = body.disabled
    await user.save_updated()
    return {"password_disabled": user.password_disabled}
