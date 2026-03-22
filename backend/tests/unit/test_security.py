"""security.py のユニットテスト (DB/Redis 不要)"""

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.core.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_api_key,
    hash_password,
    verify_password,
)
from app.core.config import settings


# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_returns_different_value_than_plain(self):
        hashed = hash_password("mypassword")
        assert hashed != "mypassword"

    def test_same_password_produces_different_hashes(self):
        """bcrypt はソルトにより毎回異なるハッシュを生成する"""
        h1 = hash_password("mypassword")
        h2 = hash_password("mypassword")
        assert h1 != h2

    def test_verify_correct_password_returns_true(self):
        hashed = hash_password("correct")
        assert verify_password("correct", hashed) is True

    def test_verify_wrong_password_returns_false(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_verify_empty_password_returns_false(self):
        hashed = hash_password("correct")
        assert verify_password("", hashed) is False


# ---------------------------------------------------------------------------
# hash_api_key
# ---------------------------------------------------------------------------

class TestHashApiKey:
    def test_returns_deterministic_hash(self):
        key = "my-api-key-12345"
        assert hash_api_key(key) == hash_api_key(key)

    def test_different_keys_produce_different_hashes(self):
        assert hash_api_key("key-a") != hash_api_key("key-b")

    def test_empty_string_produces_valid_hash(self):
        result = hash_api_key("")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_returns_hex_string(self):
        result = hash_api_key("testkey")
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# create_access_token / create_refresh_token / decode_token
# ---------------------------------------------------------------------------

class TestTokenCreation:
    def test_access_token_has_access_type(self):
        token = create_access_token("user-id-123")
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["type"] == "access"
        assert payload["sub"] == "user-id-123"

    def test_refresh_token_has_refresh_type(self):
        token = create_refresh_token("user-id-123")
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["type"] == "refresh"
        assert payload["sub"] == "user-id-123"

    def test_access_and_refresh_tokens_differ(self):
        subject = "user-id-abc"
        access = create_access_token(subject)
        refresh = create_refresh_token(subject)
        assert access != refresh


class TestDecodeToken:
    def test_decodes_valid_access_token(self):
        token = create_access_token("uid-1")
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "uid-1"
        assert payload["type"] == "access"

    def test_decodes_valid_refresh_token(self):
        token = create_refresh_token("uid-2")
        payload = decode_token(token)
        assert payload is not None
        assert payload["type"] == "refresh"

    def test_returns_none_for_tampered_token(self):
        token = create_access_token("uid-3")
        tampered = token[:-5] + "xxxxx"
        assert decode_token(tampered) is None

    def test_returns_none_for_empty_string(self):
        assert decode_token("") is None

    def test_returns_none_for_random_string(self):
        assert decode_token("not.a.jwt") is None

    def test_returns_none_for_expired_token(self):
        expire = datetime.now(UTC) - timedelta(seconds=1)
        token = jwt.encode(
            {"sub": "uid", "exp": expire, "type": "access"},
            settings.SECRET_KEY,
            algorithm=ALGORITHM,
        )
        assert decode_token(token) is None

    def test_type_mismatch_access_used_as_refresh(self):
        """access トークンを refresh 用途に使えないことを確認 (type フィールド検証はアプリ層)"""
        token = create_access_token("uid-x")
        payload = decode_token(token)
        # decode_token 自体はデコードするが type は "access"
        assert payload is not None
        assert payload["type"] == "access"
        # 呼び出し側 (auth.py の /refresh) が type != "refresh" を弾く設計
