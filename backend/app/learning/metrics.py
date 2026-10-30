"""Learning metrics over resolved decision records (pure on lists of records)."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from app.learning import stats as ST
from app.learning.attribution import ERROR_CLASSES

MIN_GROUP = 20  # accuracy by group is reported only with at least this many samples


def _acc(rows: list[Any]) -> dict[str, Any]:
    n = len(rows)
    hits = sum(1 for r in rows if r.predicted_direction == r.realized_direction)
    return {"samples": n, "accuracy": round(hits / n, 4) if n >= MIN_GROUP else None,
            "note": None if n >= MIN_GROUP else f"Needs ≥ {MIN_GROUP} samples."}


def summary(records: list[Any]) -> dict[str, Any]:
    resolved = [r for r in records if r.outcome_status == "RESOLVED"]
    directional = [r for r in resolved if r.predicted_direction in ST.CLASSES
                   and r.realized_direction in ST.CLASSES]
    # Three-class forecasts only: binary (UP/DOWN) models use a different label and are
    # scored per model in the shadow/promotion report instead.
    probabilistic = [r for r in resolved if r.probabilities
                     and set(ST.CLASSES) <= set(r.probabilities)
                     and r.realized_direction in ST.CLASSES]
    out: dict[str, Any] = {
        "records": len(records), "resolved": len(resolved),
        "pending": sum(1 for r in records if r.outcome_status == "PENDING"),
        "unresolvable": sum(1 for r in records if r.outcome_status == "UNRESOLVABLE"),
        "by_decision": dict(Counter(r.decision for r in records)),
    }
    if probabilistic:
        P = np.array([[r.probabilities.get(c, 0.0) for c in ST.CLASSES] for r in probabilistic])
        y = np.array([ST.CLASSES.index(r.realized_direction) for r in probabilistic])
        prior = np.bincount(y, minlength=3) / len(y)
        B = np.tile(prior, (len(y), 1))
        ece, table = ST.calibration_table(P, y)
        out["probabilistic"] = {
            "samples": len(y),
            "brier": round(float(ST.per_sample_brier(P, y).mean()), 5),
            "log_loss": round(float(ST.per_sample_log_loss(P, y).mean()), 5),
            "climatology_brier": round(float(ST.per_sample_brier(B, y).mean()), 5),
            "climatology_log_loss": round(float(ST.per_sample_log_loss(B, y).mean()), 5),
            "climatology_note": "In-sample class frequency of these outcomes (optimistic "
                                "baseline, for orientation only).",
            "ece": ece, "calibration": table, "class_reliability": ST.class_reliability(P, y),
        }
    else:
        out["probabilistic"] = None
    out["accuracy"] = {
        "overall": _acc(directional),
        "by_timeframe": {k: _acc([r for r in directional if r.timeframe == k])
                         for k in sorted({r.timeframe for r in directional})},
        "by_regime": {k: _acc([r for r in directional if (r.regime or "UNKNOWN") == k])
                      for k in sorted({r.regime or "UNKNOWN" for r in directional})},
        "by_strategy": {k: _acc([r for r in directional if k in (r.strategies or [])])
                        for k in sorted({s for r in directional for s in (r.strategies or [])})},
    }
    counts = Counter(c for r in resolved for c in (r.error_classes or []))
    out["error_classes"] = {c: counts.get(c, 0) for c in ERROR_CLASSES}
    out["no_trade"] = no_trade_analysis(resolved)
    return out


def no_trade_analysis(resolved: list[Any]) -> dict[str, Any]:
    """What happened after abstentions. Hypothetical: no trade was taken."""
    abst = [r for r in resolved if r.decision in ("NO_TRADE", "SKIPPED")]
    rets = [r.realized_return for r in abst if r.realized_return is not None]
    moves = Counter(r.realized_direction or "UNKNOWN" for r in abst)
    # Counterfactual: if a setup side existed, the return had it been followed.
    cf = []
    for r in abst:
        side = (r.plan or {}).get("side")
        if side in ("CE", "PE") and r.realized_return is not None:
            cf.append(r.realized_return if side == "CE" else -r.realized_return)
    return {
        "samples": len(abst), "label": "HYPOTHETICAL",
        "realized_moves": dict(moves),
        "mean_abs_return": round(float(np.mean(np.abs(rets))), 6) if rets else None,
        "counterfactual_setup_samples": len(cf),
        "counterfactual_mean_return": round(float(np.mean(cf)), 6) if cf else None,
        "note": "Hypothetical underlying returns after decisions that did not trade, before "
                "costs, premium decay and slippage. Not a performance claim.",
    }
