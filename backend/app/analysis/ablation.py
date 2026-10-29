"""Does price structure add out-of-sample value over a simple baseline?

`feature_ablation` trains the existing logistic model (app.ai.model) with the
same purged walk-forward split on two feature sets over the same rows:

- baseline: log returns over 1/3/6 bars, ATR % of price, EMA 21 slope;
- baseline + structure: the baseline plus candlestick-pattern and SMC
  features (each computed from events confirmed at or before the row).

It reports out-of-sample log loss and Brier score for both arms. Results on
synthetic DEMO data are not evidence.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.ai import features as F
from app.ai import model as M
from app.analysis import context as C
from app.analysis import patterns as PAT
from app.analysis import smc as SMC

BASELINE_FEATURES = ["ret_1", "ret_3", "ret_6", "atr_pct", "ema21_slope_3"]
STRUCTURE_FEATURES = ["pattern_net_3", "confirmed_pattern_net_5", "structure_trend",
                      "bars_since_break", "premium_discount_pos", "open_zone_net",
                      "sweep_net_5"]


def _sign(direction: str | None) -> int:
    return 1 if direction == "BULLISH" else -1 if direction == "BEARISH" else 0


def _baseline_row(ctx: C.Ctx, t: int) -> list[float] | None:
    if t < 6:
        return None
    c, a, e = ctx.c, ctx.s["atr14"][t], ctx.s["ema21"]
    if a is None or e[t] is None or e[t - 3] is None:
        return None
    return [math.log(c[t] / c[t - 1]), math.log(c[t] / c[t - 3]), math.log(c[t] / c[t - 6]),
            a / c[t], e[t] / e[t - 3] - 1]


def structure_features(ctx: C.Ctx) -> list[list[float]]:
    """One row per bar. Every value at t uses only events confirmed at ≤ t (events
    never repaint, so this equals recomputing on candles[:t+1])."""
    n = len(ctx)
    pats = PAT.candlestick_patterns(ctx)
    smc = SMC.analyse(ctx)
    det = [0] * n
    conf = [0] * n
    for p in pats:
        det[p["detection_index"]] += _sign(p["direction"])
        ci = p["confirmation"]["index"]
        if p["confirmation"]["status"] == "CONFIRMED" and ci is not None:
            conf[ci] += _sign(p["confirmation"]["direction"])
    sweeps = [0] * n
    for s in smc["liquidity_sweeps"]:
        sweeps[s["confirmed_index"]] += _sign(s["direction"])
    breaks = {e["confirmed_index"]: _sign(e["direction"]) for e in smc["structure_events"]}
    zones = smc["fair_value_gaps"] + smc["order_blocks"] + smc["supply_demand"]
    ph, pl = PAT.pivots(ctx)
    known = sorted([(i + PAT.PIVOT_R, p, "H") for i, p in ph]
                   + [(i + PAT.PIVOT_R, p, "L") for i, p in pl])
    rows: list[list[float]] = []
    trend, last_break, k = 0, None, 0
    hi = lo = None
    for t in range(n):
        if t in breaks:
            trend, last_break = breaks[t], t
        while k < len(known) and known[k][0] <= t:
            if known[k][2] == "H":
                hi = known[k][1]
            else:
                lo = known[k][1]
            k += 1
        pos = 0.5
        if hi is not None and lo is not None and hi > lo:
            pos = min(max((ctx.c[t] - lo) / (hi - lo), -0.5), 1.5)
        open_net = sum(_sign(z["direction"]) for z in zones if z["confirmed_index"] <= t
                       and (z["mitigated_index"] is None or z["mitigated_index"] > t))
        rows.append([
            float(sum(det[max(0, t - 2):t + 1])), float(sum(conf[max(0, t - 4):t + 1])),
            float(trend), min(t - last_break, 50) / 50 if last_break is not None else 1.0,
            pos, float(max(-3, min(3, open_net))), float(sum(sweeps[max(0, t - 4):t + 1])),
        ])
    return rows


def _arm(X: np.ndarray, y: np.ndarray, horizon: int, names: list[str]) -> dict[str, Any]:
    try:
        m = M.evaluate(M.walk_forward(X, y, horizon))
    except ValueError as exc:
        return {"features": names, "error": str(exc), "oos_samples": 0}
    return {"features": names, "oos_samples": m["oos_samples"], "log_loss": m["log_loss"],
            "brier": m["brier"], "climatology_log_loss": m["baseline_log_loss"],
            "climatology_brier": m["baseline_brier"], "ece": m["ece"], "folds": len(m["folds"])}


def feature_ablation(candles: list[dict[str, Any]], horizon: int = 4,
                     timeframe: str | None = None, is_demo: bool = True) -> dict[str, Any]:
    ctx = C.build(candles, intraday=timeframe != "1d", timeframe=timeframe)
    struct = structure_features(ctx)
    xb, xs, ys = [], [], []
    for t in range(len(ctx)):
        b = _baseline_row(ctx, t)
        y = F.label(ctx, t, horizon)
        if b is None or y is None:
            continue
        xb.append(b)
        xs.append(b + struct[t])
        ys.append(y)
    y_arr = np.array(ys, dtype=int)
    base = _arm(np.array(xb), y_arr, horizon, BASELINE_FEATURES)
    plus = _arm(np.array(xs), y_arr, horizon, BASELINE_FEATURES + STRUCTURE_FEATURES)
    delta = None
    if "log_loss" in base and "log_loss" in plus:
        delta = {"log_loss": round(plus["log_loss"] - base["log_loss"], 5),
                 "brier": round(plus["brier"] - base["brier"], 5),
                 "reading": "Negative deltas mean the structure features lowered out-of-sample "
                            "error on this window."}
    return {
        "horizon_bars": horizon, "rows": len(ys),
        "label_definition": F.label_definition(horizon, timeframe or "bar"),
        "validation": "Purged expanding-window walk-forward (app.ai.model.walk_forward), "
                      "same rows and folds for both arms.",
        "arms": {"baseline": base, "baseline_plus_structure": plus},
        "delta": delta,
        "evidence_status": "NOT_EVIDENCE" if is_demo else "HISTORICAL_SAMPLE",
        "note": ("SIMULATED: synthetic DEMO data; the comparison is not evidence."
                 if is_demo else "One window, one split: treat as a screening result, not proof."),
    }
