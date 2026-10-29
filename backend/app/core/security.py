"""Password hashing, JWT access tokens, opaque refresh tokens and OTP codes."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.time import utcnow

_hasher = PasswordHasher()
# Used to equalise timing when the user does not exist.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(
    settings: Settings, *, user_id: uuid.UUID, session_id: uuid.UUID, role: str
) -> tuple[str, int]:
    ttl = settings.access_token_ttl_minutes * 60
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload, settings.secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, ttl


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "iat", "sub", "sid", "type"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(settings: Settings, token: str) -> str:
    """Keyed hash for refresh tokens / OTPs stored at rest."""
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def new_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
