from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email IS NOT NULL OR phone IS NOT NULL", name="email_or_phone"),
    )

    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e]),
        default=UserRole.USER,
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    email_verified_at: Mapped[datetime | None]
    phone_verified_at: Mapped[datetime | None]
    failed_login_count: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]

    preferences: Mapped[UserPreferences] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )

    @property
    def is_verified(self) -> bool:
        return bool(self.email_verified_at or self.phone_verified_at)


class UserPreferences(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    theme: Mapped[str] = mapped_column(String(16), default="dark")
    language: Mapped[str] = mapped_column(String(16), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    reduced_motion: Mapped[bool] = mapped_column(default=False)
    haptics_enabled: Mapped[bool] = mapped_column(default=True)
    notifications: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship(back_populates="preferences")
