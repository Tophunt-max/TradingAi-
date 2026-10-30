"""Deterministic error attribution for a resolved decision (pure).

Rules (an outcome can carry several classes; order is fixed):
  DATA_QUALITY            input data quality was FAIL or WARN.
  WRONG_TIMING            a direction was predicted, the close at the horizon did not
                          confirm it, but the move in the predicted direction reached the
                          label threshold (0.5 × ATR) inside the horizon (MFE), or the
                          predicted direction was reached only after the horizon.
  WRONG_DIRECTION         a direction was predicted, the horizon outcome differs, and
                          WRONG_TIMING does not apply.
  REGIME_CHANGE           regime at resolution differs from the regime at decision.
  VOLATILITY_ERROR        realized volatility outside a predicted range (only if a range
                          was recorded).
  PATTERN_MISCLASSIFICATION a pattern cited at decision time was INVALIDATED later.
  OPTION_PRICING          a linked option position lost premium while the underlying
                          moved in the predicted direction (theta / IV / spread).
  EXECUTION_SLIPPAGE      linked fill slippage above the configured threshold.
  RISK_ORDER_MANAGEMENT   linked order REJECTED, UNKNOWN or RECONCILIATION_REQUIRED.
"""

from __future__ import annotations

from typing import Any

ERROR_CLASSES = ("DATA_QUALITY", "WRONG_DIRECTION", "WRONG_TIMING", "REGIME_CHANGE",
                 "VOLATILITY_ERROR", "PATTERN_MISCLASSIFICATION", "OPTION_PRICING",
                 "EXECUTION_SLIPPAGE", "RISK_ORDER_MANAGEMENT")
BAD_ORDER_STATES = ("REJECTED", "UNKNOWN", "RECONCILIATION_REQUIRED")


def attribute(d: dict[str, Any], o: dict[str, Any], links: dict[str, Any] | None = None,
              slippage_threshold_pct: float = 1.0) -> list[str]:
    """`d`: decision fields; `o`: computed outcome; `links`: linked order/position facts."""
    links = links or {}
    out: list[str] = []
    if d.get("data_quality") in ("FAIL", "WARN"):
        out.append("DATA_QUALITY")
    pred, real = d.get("predicted_direction"), o.get("realized_direction")
    if pred in ("UP", "DOWN") and real is not None and real != pred:
        threshold = o.get("threshold")
        mfe = o.get("max_favorable_excursion")
        late = o.get("late_direction")
        reached = threshold is not None and mfe is not None and mfe >= threshold
        out.append("WRONG_TIMING" if reached or late == pred else "WRONG_DIRECTION")
    reg0, reg1 = d.get("regime"), o.get("regime_at_resolution")
    if reg0 and reg1 and reg0 != reg1:
        out.append("REGIME_CHANGE")
    vr, rv = d.get("predicted_vol_range"), o.get("realized_volatility")
    if vr and rv is not None and not (vr.get("low", 0) <= rv <= vr.get("high", float("inf"))):
        out.append("VOLATILITY_ERROR")
    if o.get("invalidated_patterns"):
        out.append("PATTERN_MISCLASSIFICATION")
    prem = links.get("premium_change")
    ret = o.get("realized_return")
    if prem is not None and ret is not None and pred in ("UP", "DOWN"):
        moved_right = (ret > 0) if pred == "UP" else (ret < 0)
        if moved_right and prem < 0:
            out.append("OPTION_PRICING")
    slip = links.get("slippage_pct")
    if slip is not None and slip > slippage_threshold_pct:
        out.append("EXECUTION_SLIPPAGE")
    if links.get("order_status") in BAD_ORDER_STATES:
        out.append("RISK_ORDER_MANAGEMENT")
    return out
