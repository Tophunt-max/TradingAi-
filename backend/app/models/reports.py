"""Persisted daily reports (one per account and IST day; regenerated reports replace it)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, UUIDPkMixin
from app.models.accounts import MODE_CHECK, account_fk


class DailyReport(UUIDPkMixin, Base):
    __tablename__ = "daily_reports"
    __table_args__ = (
        account_fk("daily_reports"),
        UniqueConstraint("account_id", "day", name="uq_daily_reports_account_day"),
        CheckConstraint(MODE_CHECK, name="account_mode"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4))
    day: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow)
