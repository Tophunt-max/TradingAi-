"""Time helpers. UTC internally; IST only for display."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime (naive values are assumed UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_ist(value: datetime) -> datetime:
    return ensure_utc(value).astimezone(IST)
