"""Learning worker: resolves outcomes and refreshes drift reports.

Usage (from backend/):  python -m app.workers.learning [--interval 300] [--once]

Each pass (`run_once`):
  1. resolves decision records and model predictions whose horizon has elapsed;
  2. recomputes drift for PROMOTED and SHADOW models at most once per hour.
It never trains, promotes or demotes a model. Safe to run more than once at a time:
outcome rows are written once (a second worker finds nothing PENDING).
"""

from __future__ import annotations

import argparse
import logging
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.time import utcnow
from app.db.session import get_db
from app.learning import drift
from app.learning.outcomes import resolve_outcomes
from app.market_data.factory import build_provider
from app.models.ml import ModelVersion

logger = logging.getLogger("app.learning_worker")
DRIFT_EVERY = timedelta(hours=1)


def run_once(db: Session, settings: Settings, provider: Any, now: datetime) -> dict[str, Any]:
    stats: dict[str, Any] = resolve_outcomes(
        db, provider, now, slippage_threshold_pct=settings.learning_slippage_threshold_pct)
    db.commit()
    drift_stats: dict[str, str] = {}
    models = db.scalars(select(ModelVersion).where(
        ModelVersion.status.in_(("PROMOTED", "SHADOW")))).all()
    for mv in models:
        if not drift.due(db, mv.id, now, DRIFT_EVERY):
            continue
        try:
            rep = drift.compute_report(db, provider, mv, now)
            db.commit()
            drift_stats[str(mv.id)] = rep.status
        except Exception:  # one model's failure must not stop the others
            db.rollback()
            logger.exception("drift_report_failed", extra={"model_id": str(mv.id)})
    stats["drift"] = drift_stats
    return stats


def loop(stop: threading.Event, interval: float, once: bool = False) -> None:
    settings = get_settings()
    while not stop.is_set():
        db = next(get_db())
        try:
            stats = run_once(db, settings, build_provider(db, settings), utcnow())
            logger.info("learning_pass", extra={k: v for k, v in stats.items() if k != "drift"})
        except Exception:
            db.rollback()
            logger.exception("learning_pass_failed")
        finally:
            db.close()
        if once:
            break
        stop.wait(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    configure_logging("INFO")
    loop(threading.Event(), args.interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
