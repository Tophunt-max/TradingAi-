"""Persistence of daily reports: one row per account and IST day (regeneration replaces
the payload). `generate_daily_reports` is what the reports worker runs."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.accounts import Account
from app.models.execution import DailyRiskState, Fill, Position
from app.models.reports import DailyReport
from app.models.user import User
from app.reports.daily import daily_report, day_window
from app.reports.telegram import TelegramNotifier, report_ready_text

logger = logging.getLogger("app.reports")


def save_report(db: Session, account: Account, day: date, payload: dict[str, Any],
                now: datetime | None = None) -> tuple[DailyReport, bool]:
    """Upsert by (account_id, day). Returns (row, created)."""
    now = now or utcnow()
    row = db.scalar(select(DailyReport).where(DailyReport.account_id == account.id,
                                              DailyReport.day == day))
    if row is not None:
        row.payload, row.generated_at = payload, now
        db.flush()
        return row, False
    row = DailyReport(user_id=account.user_id, account_id=account.id,
                      account_mode=account.mode, day=day, payload=payload, generated_at=now)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:  # generated concurrently by another worker
        row = db.scalar(select(DailyReport).where(DailyReport.account_id == account.id,
                                                  DailyReport.day == day))
        row.payload, row.generated_at = payload, now  # type: ignore[union-attr]
        db.flush()
        return row, False  # type: ignore[return-value]
    return row, True


def accounts_with_activity(db: Session, day: date) -> list[uuid.UUID]:
    lo, hi = day_window(day)
    ids = set(db.scalars(select(Position.account_id).where(
        Position.closed_at >= lo, Position.closed_at < hi)).all())
    ids |= set(db.scalars(select(Fill.account_id).where(Fill.ts >= lo, Fill.ts < hi)).all())
    ids |= set(db.scalars(select(DailyRiskState.account_id).where(
        DailyRiskState.trading_day == day)).all())
    return sorted(ids, key=str)


def generate_daily_reports(db: Session, day: date, notifier: TelegramNotifier | None = None,
                           now: datetime | None = None,
                           notify_emails: list[str] | None = None) -> dict[str, Any]:
    """Generate and store the report of every account with activity on `day` (caller
    commits). New reports are announced through the notifier, if configured, only for users
    in `notify_emails` (the Telegram chat is server-wide)."""
    now = now or utcnow()
    allowed = {e.lower() for e in (notify_emails or [])}
    stats = {"day": day.isoformat(), "generated": 0, "updated": 0, "notified": 0}
    for account_id in accounts_with_activity(db, day):
        acc = db.get(Account, account_id)
        if acc is None:
            continue
        payload = daily_report(db, acc, acc.mode, day, now)
        _, created = save_report(db, acc, day, payload, now)
        stats["generated" if created else "updated"] += 1
        if not (created and notifier is not None):
            continue
        user = db.get(User, acc.user_id)
        if user is None or (user.email or "").lower() not in allowed:
            continue
        if notifier.send(report_ready_text(payload)):
            stats["notified"] += 1
    return stats
