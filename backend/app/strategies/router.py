"""Strategy Router v2: strategy compatibility with the regime confidence distribution.

expected_compatibility(strategy) = Σ_state confidence(state) · COMPAT[strategy][state]

The confidences are uncalibrated regime scores (analysis.regime.detect_v2), so
the result is a compatibility score, not a probability of profit. Verdicts:

- NO_TRADE: regime UNKNOWN / ABNORMAL / EVENT_UNCERTAINTY, research-only
  strategy, or AI Adaptive without a promoted model.
- INCOMPATIBLE: expected compatibility below MIN_COMPAT.
- REQUIRES_VALIDATION: compatible on paper, but no out-of-sample validation has
  been recorded for this strategy in the current state.
- COMPATIBLE: compatible and validated for the current state.

The router never produces orders; the risk manager has the final word.
"""

from __future__ import annotations

from typing import Any

from app.analysis.regime import NO_TRADE_STATES, STATES
from app.strategies.catalog import STRATEGIES, TEMPLATE_ORDER

VERSION = "router-v2.0"
MIN_COMPAT = 0.4
_S = ["STRONG_UPTREND", "STRONG_DOWNTREND", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY",
      "BREAKOUT", "REVERSAL"]


def _row(*vals: float) -> dict[str, float]:
    d = dict(zip(_S, vals, strict=True))
    return d | {"ABNORMAL": 0.0, "EVENT_UNCERTAINTY": 0.0}


# Design compatibility (0 = against the design, 1 = what it is built for). Expert priors,
# not fitted values; see docs/ai-models.md.
COMPAT: dict[str, dict[str, float]] = {
    "momentum": _row(1.0, 1.0, 0.1, 0.4, 0.2, 0.7, 0.1),
    "scalping": _row(0.7, 0.7, 0.4, 0.3, 0.3, 0.6, 0.2),
    "breakout": _row(0.5, 0.5, 0.3, 0.3, 0.6, 1.0, 0.2),
    "reversal": _row(0.1, 0.1, 0.8, 0.3, 0.4, 0.1, 1.0),
    "smc": _row(0.6, 0.6, 0.6, 0.3, 0.3, 0.6, 0.8),
    "ai_adaptive": _row(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5),
    "custom": _row(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5),
    "dca_grid": _row(0.0, 0.0, 0.6, 0.0, 0.4, 0.0, 0.2),
}


def route(regime_v2: dict[str, Any] | None, *, model_available: bool = False,
          validated: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Compatibility of every catalogue strategy with the current regime distribution.

    `validated` maps strategy id -> states in which out-of-sample validation passed.
    No such evidence is stored yet, so the default is empty.
    """
    validated = validated or {}
    state = regime_v2["state"] if regime_v2 else "UNKNOWN"
    conf = (regime_v2 or {}).get("confidence") or {}
    rows = []
    for sid in TEMPLATE_ORDER:
        meta = STRATEGIES[sid]
        compat = COMPAT[sid]
        expected = sum(conf.get(s, 0.0) * compat[s] for s in STATES) if conf else None
        reasons: list[str] = []
        if meta["research_only"]:
            verdict = "NO_TRADE"
            reasons.append("Research-only strategy; it cannot be activated.")
        elif state in NO_TRADE_STATES:
            verdict = "NO_TRADE"
            reasons.append({"UNKNOWN": "Regime is unknown or conflicting.",
                            "ABNORMAL": "Abnormal market or data-quality failure.",
                            "EVENT_UNCERTAINTY": "Event-driven uncertainty."}[state])
        elif sid == "ai_adaptive" and not model_available:
            verdict = "NO_TRADE"
            reasons.append("Needs a promoted model that passed out-of-sample checks.")
        elif expected is not None and expected < MIN_COMPAT:
            verdict = "INCOMPATIBLE"
            reasons.append(f"Expected compatibility {expected:.2f} is below {MIN_COMPAT}.")
        elif state not in validated.get(sid, set()):
            verdict = "REQUIRES_VALIDATION"
            reasons.append(f"No out-of-sample validation recorded for {meta['name']} in "
                           f"{state.replace('_', ' ').lower()} conditions.")
        else:
            verdict = "COMPATIBLE"
            reasons.append("Compatible and validated for the current state.")
        top = sorted(((s, conf.get(s, 0.0) * compat[s]) for s in STATES),
                     key=lambda kv: -kv[1])[:3] if conf else []
        rows.append({
            "id": sid, "name": meta["name"], "version": meta["version"],
            "expected_compatibility": None if expected is None else round(expected, 4),
            "compat_in_state": compat.get(state), "verdict": verdict, "reasons": reasons,
            "contributions": [{"state": s, "value": round(v, 4)} for s, v in top],
            "validation_status": "VALIDATED" if state in validated.get(sid, set())
            else "UNVALIDATED",
        })
    rows.sort(key=lambda r: -(r["expected_compatibility"] or 0))
    return {
        "version": VERSION, "regime_state": state, "min_compatibility": MIN_COMPAT,
        "calibration_status": (regime_v2 or {}).get("calibration_status", "UNCALIBRATED"),
        "no_trade": state in NO_TRADE_STATES, "strategies": rows,
        "note": "Compatibility weights are design priors applied to uncalibrated regime "
                "confidence scores. They rank fit, not profitability.",
    }
