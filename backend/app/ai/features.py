"""Versioned feature engineering and labels for the baseline model.

Features at row t use candles[0..t] only. Labels use candles t+1..t+H and are
therefore only defined for t <= n-1-H. Training code must purge H rows between
train and test folds so no label window overlaps a test feature.
"""

from __future__ import annotations

import math
from typing import Any

from app.analysis.context import Ctx
from app.core.time import IST

FEATURE_VERSION = "f1"
FEATURE_NAMES = [
    "ret_1", "ret_3", "ret_6", "close_vs_ema9", "close_vs_ema21", "close_vs_ema50",
    "ema9_vs_ema21", "rsi", "macd_hist", "atr_pct", "adx", "bb_pct_b", "log_rvol",
    "stoch_k", "body_pct", "upper_wick_pct", "lower_wick_pct", "time_of_day",
]
CLASSES = ["DOWN", "SIDEWAYS", "UP"]
BINARY_CLASSES = ["DOWN", "UP"]
LABEL_ATR_MULT = 0.5
TARGETS = ("three_class", "binary")


def _rel(a: float | None, b: float | None) -> float | None:
    return None if a is None or b in (None, 0) else (a - b) / b


def row(ctx: Ctx, t: int) -> list[float] | None:
    if t < 6:
        return None
    s, c = ctx.s, ctx.c
    a, up, lo = s["atr14"][t], s["bb_upper"][t], s["bb_lower"][t]
    rvol = s["rvol20"][t]
    rng = ctx.h[t] - ctx.l[t]
    if ctx.intraday:
        local = ctx.ts[t].astimezone(IST)
        tod = (local.hour * 60 + local.minute - 555) / 375
    else:
        tod = 0.0
    vals: list[float | None] = [
        math.log(c[t] / c[t - 1]), math.log(c[t] / c[t - 3]), math.log(c[t] / c[t - 6]),
        _rel(c[t], s["ema9"][t]), _rel(c[t], s["ema21"][t]), _rel(c[t], s["ema50"][t]),
        _rel(s["ema9"][t], s["ema21"][t]),
        None if s["rsi14"][t] is None else s["rsi14"][t] / 100,
        None if s["macd_hist"][t] is None else s["macd_hist"][t] / c[t],
        None if a is None else a / c[t],
        None if s["adx14"][t] is None else s["adx14"][t] / 100,
        None if up is None or lo is None or up == lo else (c[t] - lo) / (up - lo),
        0.0 if rvol is None else math.log(max(rvol, 1e-6)),
        None if s["stoch_k"][t] is None else s["stoch_k"][t] / 100,
        abs(c[t] - ctx.o[t]) / rng if rng else 0.0,
        (ctx.h[t] - max(c[t], ctx.o[t])) / rng if rng else 0.0,
        (min(c[t], ctx.o[t]) - ctx.l[t]) / rng if rng else 0.0,
        tod,
    ]
    if any(v is None for v in vals):
        return None
    return [float(v) for v in vals]  # type: ignore[arg-type]


def label(ctx: Ctx, t: int, horizon: int) -> int | None:
    if t + horizon >= len(ctx):
        return None
    a = ctx.s["atr14"][t]
    if a is None:
        return None
    move = ctx.c[t + horizon] - ctx.c[t]
    if move > LABEL_ATR_MULT * a:
        return 2
    if move < -LABEL_ATR_MULT * a:
        return 0
    return 1


def label_binary(ctx: Ctx, t: int, horizon: int) -> int | None:
    """UP (1) if the close H bars ahead is above the current close, DOWN (0) if
    below; exact ties are dropped (None)."""
    if t + horizon >= len(ctx):
        return None
    move = ctx.c[t + horizon] - ctx.c[t]
    return None if move == 0 else int(move > 0)


def classes_for(target: str) -> list[str]:
    return BINARY_CLASSES if target == "binary" else CLASSES


def dataset(ctx: Ctx, horizon: int, target: str = "three_class"
            ) -> tuple[list[list[float]], list[int], list[int]]:
    """(X, y, row index) for rows that have both features and a label."""
    if target not in TARGETS:
        raise ValueError(f"Unknown target {target!r}.")
    lab = label_binary if target == "binary" else label
    xs, ys, idx = [], [], []
    for t in range(len(ctx)):
        x = row(ctx, t)
        if x is None:
            continue
        y = lab(ctx, t, horizon)
        if y is None:
            continue
        xs.append(x)
        ys.append(y)
        idx.append(t)
    return xs, ys, idx


def outcomes(ctx: Ctx, idx: list[int], horizon: int) -> dict[str, list[float | None]]:
    """Realised forward quantities per labelled row (training targets only).

    ret_pct: 100 × log(close[t+H] / close[t]).
    vol_ratio: per-bar realised vol over bars t+1..t+H divided by the per-bar
    realised vol of the 20 bars up to t (rv20 / sqrt(20)); None when undefined.
    """
    rets, ratios = [], []
    for t in idx:
        rets.append(100 * math.log(ctx.c[t + horizon] / ctx.c[t]))
        fwd = [math.log(ctx.c[k] / ctx.c[k - 1]) for k in range(t + 1, t + horizon + 1)]
        cur = ctx.s["rv20"][t]
        fv = math.sqrt(sum(r * r for r in fwd) / len(fwd)) if fwd else None
        ratios.append(fv / (cur / math.sqrt(20)) if fv is not None and cur else None)
    return {"ret_pct": rets, "vol_ratio": ratios}


def label_definition(horizon: int, timeframe: str, target: str = "three_class") -> str:
    if target == "binary":
        return (f"Close {horizon} × {timeframe} bars ahead vs. the current close: UP if higher, "
                "DOWN if lower (ties dropped). No SIDEWAYS band.")
    return (f"Close {horizon} × {timeframe} bars ahead vs. the current close: UP if it rises "
            f"more than {LABEL_ATR_MULT} × ATR(14), DOWN if it falls more than that, otherwise "
            "SIDEWAYS.")


def info() -> dict[str, Any]:
    return {"version": FEATURE_VERSION, "names": FEATURE_NAMES, "classes": CLASSES}
