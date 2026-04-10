"""Tests for app.core.crypto — Fernet encrypt/decrypt utilities."""

import pytest

from app.core.crypto import (
    _derive_fernet_key,
    decrypt,
    encrypt,
)


class TestDeriveKey:
    """Key derivation from SECRET_KEY."""

    def test_returns_bytes(self):
        key = _derive_fernet_key()
        assert isinstance(key, bytes)

    def test_key_is_url_safe_base64(self):
        """Fernet requires exactly 44 bytes of URL-safe base64."""
        key = _derive_fernet_key()
        assert len(key) == 44

    def test_deterministic(self):
        """Same SECRET_KEY always produces the same derived key."""
        # lru_cache guarantees identity, but verify the contract anyway.
        assert _derive_fernet_key() is _derive_fernet_key()


class TestEncrypt:
    """encrypt() happy and error paths."""

    def test_returns_str(self):
        ct = encrypt("hello")
        assert isinstance(ct, str)

    def test_ciphertext_differs_from_plaintext(self):
        ct = encrypt("secret-value")
        assert ct != "secret-value"

    def test_different_calls_produce_different_ciphertexts(self):
        """Fernet uses a random IV, so two encryptions of the same
        plaintext must produce different ciphertexts."""
        ct1 = encrypt("same")
        ct2 = encrypt("same")
        assert ct1 != ct2

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            encrypt("")

    def test_unicode_roundtrip(self):
        """Non-ASCII (Japanese, emoji) must survive encrypt→decrypt."""
        original = "日本語テスト 🔑"
        ct = encrypt(original)
        assert decrypt(ct) == original

    def test_long_value(self):
        """Values up to 10 000 chars (the field limit) must work."""
        original = "x" * 10_000
        ct = encrypt(original)
        assert decrypt(ct) == original


class TestDecrypt:
    """decrypt() happy and error paths."""

    def test_roundtrip(self):
        plaintext = "my-api-key-12345"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_empty_ciphertext_raises(self):
        with pytest.raises(ValueError, match="empty"):
            decrypt("")

    def test_invalid_ciphertext_raises(self):
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt("not-a-valid-fernet-token")

    def test_tampered_ciphertext_raises(self):
        ct = encrypt("original")
        # Flip the last character to simulate tampering.
        tampered = ct[:-1] + ("A" if ct[-1] != "A" else "B")
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt(tampered)


class TestKeyIsolation:
    """Verify that changing SECRET_KEY breaks decryption.

    Since _derive_fernet_key uses lru_cache, we monkeypatch both the
    settings value AND clear the cache to simulate a key change.
    """

    def test_wrong_key_cannot_decrypt(self, monkeypatch):
        ct = encrypt("secret")

        # Clear the cached key so the next call re-derives.
        _derive_fernet_key.cache_clear()
        monkeypatch.setattr(
            "app.core.crypto.settings", type("S", (), {"SECRET_KEY": "different-key"})()
        )
        try:
            with pytest.raises(ValueError, match="Decryption failed"):
                decrypt(ct)
        finally:
            # Restore original cache for subsequent tests.
            _derive_fernet_key.cache_clear()
