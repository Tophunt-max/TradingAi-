"""Train (and optionally promote) a baseline model from the command line.

Usage (from backend/):
  python -m app.ai.train NIFTY 15m [--horizon 4] [--history 3000] [--promote]

--promote only succeeds when the out-of-sample gate passed on real data.
"""

from __future__ import annotations

import argparse
import json

from app.ai import registry
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.session import get_db
from app.market_data.factory import build_provider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("timeframe", choices=["5m", "15m", "1h", "1d"])
    ap.add_argument("--horizon", type=int)
    ap.add_argument("--history", type=int, default=registry.TRAIN_HISTORY)
    ap.add_argument("--promote", action="store_true")
    args = ap.parse_args(argv)
    configure_logging("WARNING")
    db = next(get_db())
    try:
        mv = registry.train_candidate(db, build_provider(db, get_settings()),
                                      symbol=args.symbol.upper(), timeframe=args.timeframe,
                                      horizon=args.horizon, history=args.history)
        db.commit()
        m = mv.metrics
        print(f"{mv.name} [{mv.id}] status={mv.status} source={mv.data_source}")
        print(f"  OOS samples {m['oos_samples']}  log loss {m['log_loss']} "
              f"(baseline {m['baseline_log_loss']})  Brier {m['brier']} "
              f"(baseline {m['baseline_brier']})  ECE {m['ece']}")
        for c in mv.gate["checks"]:
            print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['label']}: {c['detail']}")
        if args.promote:
            registry.promote(db, mv)
            db.commit()
            print("Promoted.")
        return 0
    except AppError as exc:
        db.rollback()
        print(f"Error {exc.code}: {exc.message}")
        if exc.details:
            print(json.dumps(exc.details, indent=2, default=str))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
