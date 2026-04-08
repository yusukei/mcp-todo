"""WebAuthn / Passkey endpoint tests.

WebAuthn の暗号検証自体はモックしつつ、エンドポイントのロジック
（チャレンジ保存・消費、ユーザ検索、権限チェック等）をテストする。
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.user import AuthType, User, WebAuthnCredential
from app.core.security import create_access_token, hash_password


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# Registration options
# ---------------------------------------------------------------------------


async def test_register_options_requires_auth(client: AsyncClient):
    resp = await client.post("/api/v1/auth/webauthn/register/options")
    assert resp.status_code == 401


async def test_register_options_admin_only(client: AsyncClient, regular_user, user_headers):
    resp = await client.post("/api/v1/auth/webauthn/register/options", headers=user_headers)
    assert resp.status_code == 403


async def test_register_options_success(client: AsyncClient, admin_user, admin_headers):
    resp = await client.post("/api/v1/auth/webauthn/register/options", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "challenge" in data
    assert "rp" in data
    assert data["rp"]["id"] == "localhost"
    assert "user" in data
    assert data["user"]["name"] == admin_user.email


# ---------------------------------------------------------------------------
# Registration verify
# ---------------------------------------------------------------------------


async def test_register_verify_no_challenge(client: AsyncClient, admin_user, admin_headers):
    """Verify fails when no registration challenge exists."""
    resp = await client.post(
        "/api/v1/auth/webauthn/register/verify",
        headers=admin_headers,
        json={"credential": {}, "name": "test"},
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


@patch("app.api.v1.endpoints.auth.webauthn.verify_registration_response")
async def test_register_verify_success(
    mock_verify, client: AsyncClient, admin_user, admin_headers
):
    """Full registration flow: get options → verify."""
    # 1. Get options to store challenge
    options_resp = await client.post(
        "/api/v1/auth/webauthn/register/options", headers=admin_headers
    )
    assert options_resp.status_code == 200

    # 2. Mock verification result
    cred_id = b"\x01\x02\x03\x04"
    pub_key = b"\x05\x06\x07\x08"
    mock_result = MagicMock()
    mock_result.credential_id = cred_id
    mock_result.credential_public_key = pub_key
    mock_result.sign_count = 0
    mock_verify.return_value = mock_result

    # 3. Verify with mock credential
    resp = await client.post(
        "/api/v1/auth/webauthn/register/verify",
        headers=admin_headers,
        json={
            "credential": {
                "id": _b64url_encode(cred_id),
                "rawId": _b64url_encode(cred_id),
                "response": {
                    "attestationObject": _b64url_encode(b"fake"),
                    "clientDataJSON": _b64url_encode(b"fake"),
                },
                "type": "public-key",
                "authenticatorAttachment": "platform",
            },
            "name": "My Laptop",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "My Laptop"
    assert data["credential_id"] == _b64url_encode(cred_id)

    # 4. Verify credential is stored in DB
    user = await User.get(admin_user.id)
    assert len(user.webauthn_credentials) == 1
    assert user.webauthn_credentials[0].name == "My Laptop"


# ---------------------------------------------------------------------------
# Authentication options
# ---------------------------------------------------------------------------


async def test_authenticate_options_no_auth_required(client: AsyncClient):
    """Authentication options endpoint doesn't require auth."""
    resp = await client.post("/api/v1/auth/webauthn/authenticate/options", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "challenge" in data
    assert data["rpId"] == "localhost"


async def test_authenticate_options_with_email(client: AsyncClient, admin_user):
    """When email is provided and user has credentials, they're included."""
    # Add a credential to the user
    admin_user.webauthn_credentials = [
        WebAuthnCredential(
            credential_id=_b64url_encode(b"\x01\x02\x03"),
            public_key=_b64url_encode(b"\x04\x05\x06"),
            sign_count=0,
            name="Test Key",
        )
    ]
    await admin_user.save_updated()

    resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/options",
        json={"email": admin_user.email},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data.get("allowCredentials", [])) == 1


# ---------------------------------------------------------------------------
# Authentication verify
# ---------------------------------------------------------------------------


async def test_authenticate_verify_unknown_credential(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/verify",
        json={
            "credential": {
                "id": _b64url_encode(b"\xff\xff"),
                "rawId": _b64url_encode(b"\xff\xff"),
                "response": {
                    "authenticatorData": _b64url_encode(b"fake"),
                    "clientDataJSON": _b64url_encode(
                        json.dumps({"challenge": "fakechallenge", "origin": "http://localhost:3000", "type": "webauthn.get"}).encode()
                    ),
                    "signature": _b64url_encode(b"fake"),
                },
                "type": "public-key",
                "authenticatorAttachment": "platform",
            }
        },
    )
    assert resp.status_code == 401


@patch("app.api.v1.endpoints.auth.webauthn.verify_authentication_response")
async def test_authenticate_verify_success(mock_verify, client: AsyncClient, admin_user):
    """Full authentication flow: register credential, get options, verify."""
    cred_id = b"\x10\x20\x30"
    pub_key = b"\x40\x50\x60"

    # Pre-store a credential
    admin_user.webauthn_credentials = [
        WebAuthnCredential(
            credential_id=_b64url_encode(cred_id),
            public_key=_b64url_encode(pub_key),
            sign_count=0,
            name="Test Key",
        )
    ]
    await admin_user.save_updated()

    # 1. Get authentication options
    options_resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/options",
        json={"email": admin_user.email},
    )
    assert options_resp.status_code == 200
    options = options_resp.json()
    challenge = options["challenge"]

    # 2. Mock verification
    mock_result = MagicMock()
    mock_result.new_sign_count = 1
    mock_verify.return_value = mock_result

    # 3. Build fake clientDataJSON with the real challenge
    client_data = json.dumps({
        "type": "webauthn.get",
        "challenge": challenge,
        "origin": "http://localhost:3000",
    }).encode()

    resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/verify",
        json={
            "credential": {
                "id": _b64url_encode(cred_id),
                "rawId": _b64url_encode(cred_id),
                "response": {
                    "authenticatorData": _b64url_encode(b"fake_auth_data"),
                    "clientDataJSON": _b64url_encode(client_data),
                    "signature": _b64url_encode(b"fake_sig"),
                },
                "type": "public-key",
                "authenticatorAttachment": "platform",
            }
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data

    # Verify sign count was updated
    user = await User.get(admin_user.id)
    assert user.webauthn_credentials[0].sign_count == 1


# ---------------------------------------------------------------------------
# Credential management
# ---------------------------------------------------------------------------


async def test_list_credentials_empty(client: AsyncClient, admin_user, admin_headers):
    resp = await client.get("/api/v1/auth/webauthn/credentials", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_and_delete_credentials(client: AsyncClient, admin_user, admin_headers):
    # Add credentials
    admin_user.webauthn_credentials = [
        WebAuthnCredential(
            credential_id="cred-aaa",
            public_key="key-aaa",
            sign_count=0,
            name="Key A",
        ),
        WebAuthnCredential(
            credential_id="cred-bbb",
            public_key="key-bbb",
            sign_count=0,
            name="Key B",
        ),
    ]
    await admin_user.save_updated()

    # List
    resp = await client.get("/api/v1/auth/webauthn/credentials", headers=admin_headers)
    assert resp.status_code == 200
    creds = resp.json()
    assert len(creds) == 2

    # Delete one
    resp = await client.delete("/api/v1/auth/webauthn/credentials/cred-aaa", headers=admin_headers)
    assert resp.status_code == 200

    # Verify
    resp = await client.get("/api/v1/auth/webauthn/credentials", headers=admin_headers)
    assert len(resp.json()) == 1
    assert resp.json()[0]["credential_id"] == "cred-bbb"


async def test_delete_nonexistent_credential(client: AsyncClient, admin_user, admin_headers):
    resp = await client.delete("/api/v1/auth/webauthn/credentials/nonexistent", headers=admin_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Inactive user
# ---------------------------------------------------------------------------


async def test_authenticate_inactive_user(client: AsyncClient, inactive_user):
    """Inactive users cannot authenticate via passkey."""
    cred_id = b"\xaa\xbb"

    inactive_user.webauthn_credentials = [
        WebAuthnCredential(
            credential_id=_b64url_encode(cred_id),
            public_key=_b64url_encode(b"\xcc\xdd"),
            sign_count=0,
            name="Key",
        )
    ]
    await inactive_user.save_updated()

    # Build fake client data
    options_resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/options", json={}
    )
    challenge = options_resp.json()["challenge"]

    client_data = json.dumps({
        "type": "webauthn.get",
        "challenge": challenge,
        "origin": "http://localhost:3000",
    }).encode()

    resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/verify",
        json={
            "credential": {
                "id": _b64url_encode(cred_id),
                "rawId": _b64url_encode(cred_id),
                "response": {
                    "authenticatorData": _b64url_encode(b"fake"),
                    "clientDataJSON": _b64url_encode(client_data),
                    "signature": _b64url_encode(b"fake"),
                },
                "type": "public-key",
                "authenticatorAttachment": "platform",
            }
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Password disable / enable
# ---------------------------------------------------------------------------


async def test_toggle_password_requires_passkey(client: AsyncClient, admin_user, admin_headers):
    """Cannot disable password without any passkeys registered."""
    resp = await client.patch(
        "/api/v1/auth/webauthn/password-disabled",
        headers=admin_headers,
        json={"disabled": True},
    )
    assert resp.status_code == 400
    assert "passkey" in resp.json()["detail"].lower()


async def test_toggle_password_disabled(client: AsyncClient, admin_user, admin_headers):
    """Can disable password when passkeys exist."""
    admin_user.webauthn_credentials = [
        WebAuthnCredential(credential_id="cred-x", public_key="key-x", sign_count=0, name="Key"),
    ]
    await admin_user.save_updated()

    # Disable password
    resp = await client.patch(
        "/api/v1/auth/webauthn/password-disabled",
        headers=admin_headers,
        json={"disabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["password_disabled"] is True

    # Verify login is blocked
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": admin_user.email, "password": "adminpass"},
    )
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()

    # Re-enable password
    resp = await client.patch(
        "/api/v1/auth/webauthn/password-disabled",
        headers=admin_headers,
        json={"disabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["password_disabled"] is False

    # Login works again
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": admin_user.email, "password": "adminpass"},
    )
    assert resp.status_code == 200


async def test_cannot_delete_last_passkey_when_password_disabled(
    client: AsyncClient, admin_user, admin_headers
):
    """Cannot delete the last passkey if password login is disabled."""
    admin_user.webauthn_credentials = [
        WebAuthnCredential(credential_id="only-key", public_key="pk", sign_count=0, name="Only Key"),
    ]
    admin_user.password_disabled = True
    await admin_user.save_updated()

    resp = await client.delete("/api/v1/auth/webauthn/credentials/only-key", headers=admin_headers)
    assert resp.status_code == 400
    assert "last passkey" in resp.json()["detail"].lower()

    # Credential still exists
    user = await User.get(admin_user.id)
    assert len(user.webauthn_credentials) == 1


async def test_toggle_password_google_user_rejected(client: AsyncClient, regular_user, user_headers):
    """Google OAuth users cannot toggle password setting."""
    resp = await client.patch(
        "/api/v1/auth/webauthn/password-disabled",
        headers=user_headers,
        json={"disabled": True},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Challenge user binding (G1 / WebAuthn spec compliance)
# ---------------------------------------------------------------------------


@patch("app.api.v1.endpoints.auth.webauthn.verify_authentication_response")
async def test_authenticate_verify_rejects_cross_user_challenge(
    mock_verify, client: AsyncClient, admin_user
):
    """Challenge issued for user A must NOT be consumable by user B's credential.

    Setup:
      - Two admin users, each with their own passkey
      - Issue an authentication challenge for user A (email pre-filter)
      - Try to consume that same challenge with user B's credential
    Expectation:
      - 401 "Challenge user mismatch"
    """
    cred_a = b"\xa1\xa2\xa3"
    cred_b = b"\xb1\xb2\xb3"

    # User A (the original admin_user fixture)
    admin_user.webauthn_credentials = [
        WebAuthnCredential(
            credential_id=_b64url_encode(cred_a),
            public_key=_b64url_encode(b"pk-a"),
            sign_count=0,
            name="A's Key",
        )
    ]
    await admin_user.save_updated()

    # User B — second admin
    user_b = User(
        email="userb@test.com",
        name="User B",
        password_hash=hash_password("pwbpwbpw"),
        auth_type=AuthType.admin,
        is_admin=True,
        webauthn_credentials=[
            WebAuthnCredential(
                credential_id=_b64url_encode(cred_b),
                public_key=_b64url_encode(b"pk-b"),
                sign_count=0,
                name="B's Key",
            )
        ],
    )
    await user_b.insert()

    # Issue authentication options scoped to user A.
    options_resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/options",
        json={"email": admin_user.email},
    )
    assert options_resp.status_code == 200
    challenge = options_resp.json()["challenge"]

    # Mock the underlying crypto so the test only exercises *our*
    # binding logic — the lib would otherwise reject the fake assertion.
    mock_result = MagicMock()
    mock_result.new_sign_count = 1
    mock_verify.return_value = mock_result

    client_data = json.dumps({
        "type": "webauthn.get",
        "challenge": challenge,
        "origin": "http://localhost:3000",
    }).encode()

    # Try to consume A's challenge with B's credential.
    resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/verify",
        json={
            "credential": {
                "id": _b64url_encode(cred_b),
                "rawId": _b64url_encode(cred_b),
                "response": {
                    "authenticatorData": _b64url_encode(b"fake"),
                    "clientDataJSON": _b64url_encode(client_data),
                    "signature": _b64url_encode(b"fake"),
                },
                "type": "public-key",
                "authenticatorAttachment": "platform",
            }
        },
    )
    assert resp.status_code == 401
    assert "mismatch" in resp.json()["detail"].lower()


@patch("app.api.v1.endpoints.auth.webauthn.verify_authentication_response")
async def test_discoverable_challenge_accepts_any_user(
    mock_verify, client: AsyncClient, admin_user
):
    """email を指定せずに発行した challenge ("*") はどのユーザのクレデンシャルでも通る。

    これは passkey の "discoverable credential" フローで必要な挙動。
    user-bind の追加で誤って壊していないことを確認する。
    """
    cred_id = b"\xc1\xc2\xc3"
    admin_user.webauthn_credentials = [
        WebAuthnCredential(
            credential_id=_b64url_encode(cred_id),
            public_key=_b64url_encode(b"pk"),
            sign_count=0,
            name="Discoverable",
        )
    ]
    await admin_user.save_updated()

    # email 指定なし → "*" として保存
    options_resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/options",
        json={},
    )
    challenge = options_resp.json()["challenge"]

    mock_result = MagicMock()
    mock_result.new_sign_count = 1
    mock_verify.return_value = mock_result

    client_data = json.dumps({
        "type": "webauthn.get",
        "challenge": challenge,
        "origin": "http://localhost:3000",
    }).encode()

    resp = await client.post(
        "/api/v1/auth/webauthn/authenticate/verify",
        json={
            "credential": {
                "id": _b64url_encode(cred_id),
                "rawId": _b64url_encode(cred_id),
                "response": {
                    "authenticatorData": _b64url_encode(b"fake"),
                    "clientDataJSON": _b64url_encode(client_data),
                    "signature": _b64url_encode(b"fake"),
                },
                "type": "public-key",
                "authenticatorAttachment": "platform",
            }
        },
    )
    assert resp.status_code == 200
