"""Reports worker: stores each account's daily report after the session.

Usage (from backend/):  python -m app.workers.reports [--once] [--day YYYY-MM-DD] [--interval 600]

Without --day it reports today once the IST clock is past 16:00 (after the 15:30 close),
otherwise yesterday. Re-running replaces the stored payload (one row per account and day);
only the first generation sends the optional Telegram "report ready" message.
"""

from __future__ import annotations

import argparse
import logging
import threading
from datetime import date, datetime, time, timedelta

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import IST, utcnow
from app.db.session import get_db
from app.reports.store import generate_daily_reports
from app.reports.telegram import TelegramNotifier

logger = logging.getLogger("app.reports_worker")
REPORT_AFTER_IST = time(16, 0)


def default_day(now: datetime) -> date:
    local = now.astimezone(IST)
    return local.date() if local.time() >= REPORT_AFTER_IST else local.date() - timedelta(days=1)


def run_once(day: date | None = None) -> dict:
    settings = get_settings()
    notifier = TelegramNotifier.from_settings(settings)
    db = next(get_db())
    try:
        stats = generate_daily_reports(db, day or default_day(utcnow()), notifier,
                                       notify_emails=settings.telegram_user_emails)
        db.commit()
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        if notifier is not None:
            notifier.close()


def loop(stop: threading.Event, interval: float) -> None:
    last: date | None = None
    while not stop.is_set():
        day = default_day(utcnow())
        if day != last:
            try:
                logger.info("reports_pass", extra=run_once(day))
                last = day
            except Exception:
                logger.exception("reports_pass_failed")
        stop.wait(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--day", type=date.fromisoformat, default=None)
    ap.add_argument("--interval", type=float, default=600.0)
    args = ap.parse_args(argv)
    configure_logging("INFO")
    if args.once or args.day:
        logger.info("reports_pass", extra=run_once(args.day))
        return 0
    loop(threading.Event(), args.interval)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
