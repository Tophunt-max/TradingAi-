"""Train a model from the command line and optionally start its SHADOW stage.

Usage (from backend/):
  python -m app.ai.train NIFTY 15m [--horizon 4] [--history 3000]
      [--model-type logistic|random_forest|hist_gb] [--target three_class|binary]
      [--seed 0] [--shadow]

--shadow moves the model to SHADOW only if every offline promotion criterion passes
(see docs/learning.md). The CLI cannot promote: PROMOTED needs shadow outcomes and the
admin API (POST /api/v1/admin/models/{id}/promote).
"""

from __future__ import annotations

import argparse
import json

from app.ai import registry
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.session import get_db
from app.learning import promotion
from app.market_data.factory import build_provider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("timeframe", choices=["5m", "15m", "1h", "1d"])
    ap.add_argument("--horizon", type=int)
    ap.add_argument("--history", type=int, default=registry.TRAIN_HISTORY)
    ap.add_argument("--model-type", default="logistic",
                    choices=["logistic", "random_forest", "hist_gb"])
    ap.add_argument("--target", default="three_class", choices=["three_class", "binary"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--shadow", action="store_true")
    args = ap.parse_args(argv)
    configure_logging("WARNING")
    db = next(get_db())
    try:
        mv = registry.train_candidate(db, build_provider(db, get_settings()),
                                      symbol=args.symbol.upper(), timeframe=args.timeframe,
                                      horizon=args.horizon, history=args.history,
                                      model_type=args.model_type, target=args.target,
                                      seed=args.seed)
        db.commit()
        m = mv.metrics
        print(f"{mv.name} [{mv.id}] status={mv.status} source={mv.data_source}")
        print(f"  OOS samples {m['oos_samples']}  log loss {m['log_loss']} "
              f"(baseline {m['baseline_log_loss']})  Brier {m['brier']} "
              f"(baseline {m['baseline_brier']})  ECE {m['ece']}  "
              f"calibration {m.get('calibration_status')}")
        if m.get("vs_logistic"):
            v = m["vs_logistic"]
            print(f"  vs logistic: Δ log loss {v['mean_diff']:+.4f} "
                  f"95% CI [{v['ci_low']:+.4f}, {v['ci_high']:+.4f}] better={v['better']}")
        for c in mv.gate["checks"]:
            print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['label']}: {c['detail']}")
        if args.shadow:
            promotion.to_shadow(db, mv)
            db.commit()
            print("Moved to SHADOW. Predictions are recorded but not served until promoted.")
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
