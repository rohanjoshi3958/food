import hashlib
import hmac
import secrets
from datetime import UTC, datetime

import bcrypt

from app.config import settings

SESSION_COOKIE_NAME = "food_session"

# Precomputed bcrypt hash so failed logins take similar time when no user exists.
_DUMMY_PASSWORD_HASH: str | None = None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    except ValueError:
        return False


def dummy_password_hash() -> str:
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password("invalid-dummy-password")
    return _DUMMY_PASSWORD_HASH


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw_token: str) -> str:
    return hmac.new(
        settings.auth_secret.encode(),
        raw_token.encode(),
        hashlib.sha256,
    ).hexdigest()


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_expired(expires_at: datetime, *, now: datetime | None = None) -> bool:
    current = as_utc(now or datetime.now(UTC))
    return as_utc(expires_at) <= current
