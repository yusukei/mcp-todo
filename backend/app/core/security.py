import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_access_token(subject: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": subject, "exp": expire, "type": "access"},
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )


def create_refresh_token(subject: str) -> tuple[str, str]:
    """Create a refresh token and return (token, jti) tuple."""
    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    jti = uuid.uuid4().hex
    token = jwt.encode(
        {"sub": subject, "exp": expire, "type": "refresh", "jti": jti},
        settings.REFRESH_SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return token, jti


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.InvalidTokenError:
        return None


def decode_refresh_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.REFRESH_SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except jwt.InvalidTokenError:
        return None
