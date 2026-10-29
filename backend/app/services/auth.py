"""Authentication use-cases: registration, verification, login, refresh, reset."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, AuthenticationError, ConflictError
from app.core.rate_limit import client_ip
from app.core.security import (
    constant_time_equals,
    create_access_token,
    hash_password,
    hash_token,
    new_otp_code,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.core.time import ensure_utc, utcnow
from app.models.auth import (
    RefreshToken,
    UserSession,
    VerificationChannel,
    VerificationCode,
    VerificationPurpose,
)
from app.models.user import User, UserPreferences
from app.schemas.auth import RegisterRequest
from app.schemas.identity import parse_identifier
from app.services.audit import record_audit
from app.services.otp_delivery import OtpSender

logger = logging.getLogger("app.auth")

RESEND_COOLDOWN = timedelta(seconds=30)


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


def find_user_by_identifier(db: Session, identifier: str) -> User | None:
    ident = parse_identifier(identifier)
    column = User.email if ident.kind == "email" else User.phone
    return db.scalar(select(User).where(column == ident.value))


def _channel_for(identifier: str) -> tuple[VerificationChannel, str]:
    ident = parse_identifier(identifier)
    if ident.kind == "email":
        return VerificationChannel.EMAIL, ident.value
    return VerificationChannel.SMS, ident.value


# --------------------------------------------------------------------------- codes


def issue_code(
    db: Session,
    settings: Settings,
    sender: OtpSender,
    *,
    user: User,
    purpose: VerificationPurpose,
    identifier: str,
) -> bool:
    """Create and deliver a one-time code. Returns False if within the cooldown."""
    now = utcnow()
    latest = db.scalar(
        select(VerificationCode)
        .where(
            VerificationCode.user_id == user.id,
            VerificationCode.purpose == purpose,
            VerificationCode.consumed_at.is_(None),
        )
        .order_by(VerificationCode.created_at.desc())
        .limit(1)
    )
    if latest and now - ensure_utc(latest.created_at) < RESEND_COOLDOWN:
        return False

    # Invalidate older outstanding codes for this purpose.
    db.execute(
        update(VerificationCode)
        .where(
            VerificationCode.user_id == user.id,
            VerificationCode.purpose == purpose,
            VerificationCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    channel, destination = _channel_for(identifier)
    code = new_otp_code()
    db.add(
        VerificationCode(
            user_id=user.id,
            purpose=purpose,
            channel=channel,
            code_hash=hash_token(settings, code),
            expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
        )
    )
    db.flush()
    sender.send(channel, destination, code, purpose)
    return True


def consume_code(
    db: Session,
    settings: Settings,
    *,
    user: User,
    purpose: VerificationPurpose,
    code: str,
) -> VerificationCode:
    now = utcnow()
    record = db.scalar(
        select(VerificationCode)
        .where(
            VerificationCode.user_id == user.id,
            VerificationCode.purpose == purpose,
            VerificationCode.consumed_at.is_(None),
        )
        .order_by(VerificationCode.created_at.desc())
        .limit(1)
    )
    invalid = AppError("The code is invalid or has expired.", code="INVALID_CODE")
    if record is None or ensure_utc(record.expires_at) <= now:
        raise invalid
    if record.attempts >= settings.otp_max_attempts:
        record.consumed_at = now
        db.commit()
        raise invalid
    if not constant_time_equals(record.code_hash, hash_token(settings, code)):
        record.attempts += 1
        if record.attempts >= settings.otp_max_attempts:
            record.consumed_at = now
        db.commit()
        raise invalid
    record.consumed_at = now
    return record


# --------------------------------------------------------------------- registration


def register_user(
    db: Session,
    settings: Settings,
    sender: OtpSender,
    req: RegisterRequest,
    request: Request,
) -> tuple[User, VerificationChannel]:
    if req.email and db.scalar(select(User.id).where(User.email == req.email)):
        raise ConflictError("An account with this email already exists.", code="ACCOUNT_EXISTS")
    if req.phone and db.scalar(select(User.id).where(User.phone == req.phone)):
        raise ConflictError("An account with this mobile already exists.", code="ACCOUNT_EXISTS")

    user = User(
        full_name=req.full_name,
        email=req.email,
        phone=req.phone,
        password_hash=hash_password(req.password),
    )
    user.preferences = UserPreferences(
        notifications={k: True for k in NOTIFICATION_KEYS},
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("An account with these details already exists.") from exc

    identifier = req.email or req.phone
    assert identifier is not None
    issue_code(
        db, settings, sender, user=user, purpose=VerificationPurpose.VERIFY_ACCOUNT,
        identifier=identifier,
    )
    record_audit(
        db,
        action="auth.register",
        user_id=user.id,
        request=request,
        details={"risk_disclosure_accepted": True},
    )
    db.commit()
    channel, _ = _channel_for(identifier)
    return user, channel


NOTIFICATION_KEYS = (
    "ai_signals",
    "bot_status",
    "order_updates",
    "risk_alerts",
    "data_alerts",
    "broker_alerts",
    "daily_report",
)


def verify_account(
    db: Session,
    settings: Settings,
    *,
    identifier: str,
    code: str,
    request: Request,
    device_name: str | None,
) -> IssuedTokens:
    user = find_user_by_identifier(db, identifier)
    if user is None or not user.is_active:
        raise AppError("The code is invalid or has expired.", code="INVALID_CODE")
    record = consume_code(
        db, settings, user=user, purpose=VerificationPurpose.VERIFY_ACCOUNT, code=code
    )
    now = utcnow()
    if record.channel == VerificationChannel.EMAIL:
        user.email_verified_at = now
    else:
        user.phone_verified_at = now
    record_audit(db, action="auth.verify", user_id=user.id, request=request,
                 details={"channel": record.channel.value})
    return create_session(db, settings, user=user, request=request, device_name=device_name)


# ---------------------------------------------------------------------------- login


def authenticate(
    db: Session,
    settings: Settings,
    *,
    identifier: str,
    password: str,
    request: Request,
) -> User:
    user = find_user_by_identifier(db, identifier)
    now = utcnow()
    bad = AuthenticationError("Invalid credentials.", code="INVALID_CREDENTIALS")

    if user is None:
        verify_password(password, None)  # equalise timing
        record_audit(db, action="auth.login", request=request, outcome="FAILURE",
                     details={"reason": "unknown_identifier"})
        db.commit()
        raise bad

    if user.locked_until and ensure_utc(user.locked_until) > now:
        retry = int((ensure_utc(user.locked_until) - now).total_seconds()) + 1
        raise AppError(
            "Too many failed attempts. Try again later.",
            code="ACCOUNT_LOCKED",
            status_code=429,
            headers={"Retry-After": str(retry)},
        )

    if not verify_password(password, user.password_hash) or not user.is_active:
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
            record_audit(db, action="auth.lockout", user_id=user.id, request=request,
                         outcome="FAILURE")
        record_audit(db, action="auth.login", user_id=user.id, request=request,
                     outcome="FAILURE", details={"reason": "bad_password"})
        db.commit()
        raise bad

    user.failed_login_count = 0
    user.locked_until = None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    if not user.is_verified:
        db.commit()
        raise AppError(
            "Verify your account before signing in.",
            code="VERIFICATION_REQUIRED",
            status_code=403,
        )
    return user


def create_session(
    db: Session,
    settings: Settings,
    *,
    user: User,
    request: Request,
    device_name: str | None,
) -> IssuedTokens:
    now = utcnow()
    session = UserSession(
        user_id=user.id,
        user_agent=request.headers.get("user-agent", "")[:255] or None,
        ip_address=client_ip(request),
        device_name=device_name,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        last_used_at=now,
    )
    db.add(session)
    db.flush()
    refresh = _new_refresh(db, settings, session)
    user.last_login_at = now
    record_audit(db, action="auth.session_created", user_id=user.id, request=request,
                 entity_type="session", entity_id=str(session.id))
    db.commit()
    access, ttl = create_access_token(
        settings, user_id=user.id, session_id=session.id, role=user.role.value
    )
    return IssuedTokens(access, refresh, ttl, user)


def _new_refresh(db: Session, settings: Settings, session: UserSession) -> str:
    token = new_refresh_token()
    db.add(
        RefreshToken(
            session_id=session.id,
            token_hash=hash_token(settings, token),
            expires_at=session.expires_at,
        )
    )
    return token


def rotate_refresh(
    db: Session, settings: Settings, *, token: str, request: Request
) -> IssuedTokens:
    now = utcnow()
    invalid = AuthenticationError("Session expired. Please sign in again.",
                                  code="INVALID_REFRESH_TOKEN")
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(settings, token))
    )
    if record is None:
        raise invalid
    session = record.session
    if record.used_at is not None:
        # Token re-use: likely theft. Revoke the whole session.
        if session.revoked_at is None:
            session.revoked_at = now
            session.revoked_reason = "refresh_token_reuse"
        record_audit(db, action="auth.refresh_reuse_detected", user_id=session.user_id,
                     request=request, outcome="FAILURE", entity_type="session",
                     entity_id=str(session.id))
        db.commit()
        raise invalid
    if session.revoked_at is not None or ensure_utc(record.expires_at) <= now:
        raise invalid

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise invalid

    record.used_at = now
    session.last_used_at = now
    new_token = _new_refresh(db, settings, session)
    db.commit()
    access, ttl = create_access_token(
        settings, user_id=user.id, session_id=session.id, role=user.role.value
    )
    return IssuedTokens(access, new_token, ttl, user)


def revoke_session(db: Session, session: UserSession, reason: str) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoked_reason = reason


def revoke_all_sessions(db: Session, user_id: uuid.UUID, reason: str,
                        except_id: uuid.UUID | None = None) -> int:
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    if except_id is not None:
        stmt = stmt.where(UserSession.id != except_id)
    return db.execute(stmt).rowcount or 0


# ------------------------------------------------------------------- password reset


def start_password_reset(
    db: Session, settings: Settings, sender: OtpSender, *, identifier: str, request: Request
) -> None:
    """Always succeeds from the caller's view to avoid account enumeration."""
    user = find_user_by_identifier(db, identifier)
    if user is None or not user.is_active:
        record_audit(db, action="auth.password_reset_requested", request=request,
                     outcome="IGNORED")
        db.commit()
        return
    issue_code(db, settings, sender, user=user, purpose=VerificationPurpose.PASSWORD_RESET,
               identifier=identifier)
    record_audit(db, action="auth.password_reset_requested", user_id=user.id, request=request)
    db.commit()


def complete_password_reset(
    db: Session, settings: Settings, *, identifier: str, code: str, new_password: str,
    request: Request,
) -> None:
    user = find_user_by_identifier(db, identifier)
    if user is None or not user.is_active:
        raise AppError("The code is invalid or has expired.", code="INVALID_CODE")
    record = consume_code(db, settings, user=user, purpose=VerificationPurpose.PASSWORD_RESET,
                          code=code)
    user.password_hash = hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    # Receiving the code proves control of that channel.
    if record.channel == VerificationChannel.EMAIL and not user.email_verified_at:
        user.email_verified_at = utcnow()
    if record.channel == VerificationChannel.SMS and not user.phone_verified_at:
        user.phone_verified_at = utcnow()
    revoke_all_sessions(db, user.id, "password_reset")
    record_audit(db, action="auth.password_reset", user_id=user.id, request=request)
    db.commit()
