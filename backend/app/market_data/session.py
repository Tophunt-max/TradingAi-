"""NSE cash/F&O regular session calendar (IST).

Weekend rules and session times are built in. Exchange holidays are NOT
bundled, because guessing them would be worse than not knowing: configure
MARKET_HOLIDAYS from the official NSE circular each year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.core.time import IST, ensure_utc

SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


@dataclass(frozen=True)
class SessionState:
    is_open: bool
    reason: str
    holidays_configured: bool


def _holidays(raw: list[str]) -> set[date]:
    out = set()
    for d in raw:
        try:
            out.add(date.fromisoformat(d.strip()))
        except ValueError:
            continue
    return out


def session_state(now: datetime, holidays: list[str]) -> SessionState:
    local = ensure_utc(now).astimezone(IST)
    hol = _holidays(holidays)
    configured = bool(hol)
    if local.weekday() >= 5:
        return SessionState(False, "Weekend", configured)
    if local.date() in hol:
        return SessionState(False, "Exchange holiday", configured)
    if local.time() < SESSION_OPEN:
        return SessionState(False, "Pre-open", configured)
    if local.time() >= SESSION_CLOSE:
        return SessionState(False, "Market closed for the day", configured)
    return SessionState(True, "Market open", configured)


def is_trading_day(d: date, holidays: list[str]) -> bool:
    return d.weekday() < 5 and d not in _holidays(holidays)


def expected_intraday_starts(d: date, minutes: int) -> list[datetime]:
    """Bucket open times for one session day, anchored to 09:15 IST."""
    start = datetime.combine(d, SESSION_OPEN, IST)
    end = datetime.combine(d, SESSION_CLOSE, IST)
    out, t = [], start
    while t < end:
        out.append(t)
        t += timedelta(minutes=minutes)
    return out
