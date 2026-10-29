from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class UserSession(UUIDPkMixin, TimestampMixin, Base):
    """A logged-in device. Access tokens reference it via the `sid` claim."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    device_name: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(String(64))

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class RefreshToken(UUIDPkMixin, TimestampMixin, Base):
    """Rotating refresh token. Re-use of a consumed token revokes the session."""

    __tablename__ = "refresh_tokens"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]

    session: Mapped[UserSession] = relationship(back_populates="refresh_tokens")


class VerificationPurpose(StrEnum):
    VERIFY_ACCOUNT = "VERIFY_ACCOUNT"
    PASSWORD_RESET = "PASSWORD_RESET"  # noqa: S105 (enum label)


class VerificationChannel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"


class VerificationCode(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "verification_codes"
    __table_args__ = (Index("ix_verification_codes_user_purpose", "user_id", "purpose"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[VerificationPurpose] = mapped_column(
        Enum(VerificationPurpose, name="verification_purpose")
    )
    channel: Mapped[VerificationChannel] = mapped_column(
        Enum(VerificationChannel, name="verification_channel")
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None]
