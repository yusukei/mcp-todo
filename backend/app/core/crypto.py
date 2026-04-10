"""Fernet encryption utilities for the Secret Store.

Derives a Fernet key from ``settings.SECRET_KEY`` via HKDF so the
same master secret that signs JWTs can also protect stored secrets
without reusing the raw key material directly.

Usage::

    from app.core.crypto import encrypt, decrypt

    ciphertext = encrypt("my-api-key-value")
    plaintext  = decrypt(ciphertext)
"""

from __future__ import annotations

import base64
import functools

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from .config import settings

# Fixed salt — changing this invalidates every existing ciphertext.
_HKDF_SALT = b"mcp-todo-secrets"
_HKDF_INFO = b"fernet-key"


@functools.lru_cache(maxsize=1)
def _derive_fernet_key() -> bytes:
    """Derive a 32-byte Fernet key from ``SECRET_KEY`` using HKDF-SHA256.

    The result is cached for the process lifetime.  ``SECRET_KEY`` is
    immutable once the application boots, so caching is safe and avoids
    repeated KDF computation on every encrypt/decrypt call.

    Returns 44-byte URL-safe base64 (what ``Fernet()`` expects).
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    raw = hkdf.derive(settings.SECRET_KEY.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _get_fernet() -> Fernet:
    return Fernet(_derive_fernet_key())


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* and return a URL-safe base64 ciphertext string.

    The returned string is safe to store in MongoDB as a regular ``str``
    field.  Fernet embeds a timestamp so the encryption time is
    verifiable on decryption.

    Raises ``ValueError`` if *plaintext* is empty.
    """
    if not plaintext:
        raise ValueError("Cannot encrypt empty string")
    token: bytes = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext string back to plaintext.

    Raises ``ValueError`` if *ciphertext* is invalid or was encrypted
    with a different key.
    """
    if not ciphertext:
        raise ValueError("Cannot decrypt empty string")
    try:
        plaintext_bytes: bytes = _get_fernet().decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise ValueError("Decryption failed — invalid ciphertext or wrong key") from exc
    return plaintext_bytes.decode("utf-8")
