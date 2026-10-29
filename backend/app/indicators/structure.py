"""Swing structure, divergences, trendlines, channels and ranges (ta-3).

Everything here is built from CONFIRMED pivots: a swing at index i is only
known at i + right. Each function takes an `as_of` index (default: the last
bar) and uses only pivots confirmed at or before it, so results computed on a
prefix of the data equal the full-data results for the same `as_of`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from app.indicators.core import confirmed_pivots

LEFT = RIGHT = 3


def swing_points(highs: Sequence[float], lows: Sequence[float], left: int = LEFT,
                 right: int = RIGHT, as_of: int | None = None) -> list[dict[str, Any]]:
    """Confirmed swing highs/lows in time order, labelled HH/LH/EH and HL/LL/EL
    against the previous swing of the same kind."""
    t = len(highs) - 1 if as_of is None else as_of
    ph, pl = confirmed_pivots(highs[:t + 1], lows[:t + 1], left, right)
    pts = sorted([(i, p, "HIGH") for i, p in ph] + [(i, p, "LOW") for i, p in pl])
    out: list[dict[str, Any]] = []
    prev: dict[str, float] = {}
    for i, p, kind in pts:
        last = prev.get(kind)
        if last is None:
            label = "H" if kind == "HIGH" else "L"
        elif kind == "HIGH":
            label = "HH" if p > last else "LH" if p < last else "EH"
        else:
            label = "HL" if p > last else "LL" if p < last else "EL"
        prev[kind] = p
        out.append({"kind": kind, "index": i, "confirmed_index": i + right, "price": p,
                    "label": label})
    return out


def divergences(highs: Sequence[float], lows: Sequence[float], osc: Sequence[float | None],
                name: str = "RSI", left: int = LEFT, right: int = RIGHT, min_gap: int = 5,
                max_gap: int = 60, hidden: bool = True) -> list[dict[str, Any]]:
    """Price/oscillator divergence between consecutive confirmed swings.

    Regular bullish: lower low in price, higher low in the oscillator.
    Regular bearish: higher high in price, lower high in the oscillator.
    Hidden bullish: higher low in price, lower low in the oscillator.
    Hidden bearish: lower high in price, higher high in the oscillator.
    Oscillator values are read at the price pivot bars. `detection_index` is
    the second pivot bar; `confirmed_index` = detection_index + right, the
    first bar at which that pivot (and so the divergence) is known.
    """
    ph, pl = confirmed_pivots(highs, lows, left, right)
    out: list[dict[str, Any]] = []
    for pts, is_high in ((pl, False), (ph, True)):
        for (i1, p1), (i2, p2) in zip(pts, pts[1:], strict=False):
            o1, o2 = osc[i1], osc[i2]
            if o1 is None or o2 is None or not (min_gap <= i2 - i1 <= max_gap):
                continue
            kind = direction = None
            if not is_high and p2 < p1 and o2 > o1:
                kind, direction = "regular", "BULLISH"
            elif not is_high and p2 > p1 and o2 < o1 and hidden:
                kind, direction = "hidden", "BULLISH"
            elif is_high and p2 > p1 and o2 < o1:
                kind, direction = "regular", "BEARISH"
            elif is_high and p2 < p1 and o2 > o1 and hidden:
                kind, direction = "hidden", "BEARISH"
            if kind is None:
                continue
            out.append({
                "name": f"{'Bullish' if direction == 'BULLISH' else 'Bearish'} "
                        f"{'hidden ' if kind == 'hidden' else ''}{name} divergence",
                "direction": direction, "kind": kind, "oscillator": name,
                "index": i1, "detection_index": i2, "confirmed_index": i2 + right,
                "price": [p1, p2], "oscillator_values": [o1, o2],
            })
    return sorted(out, key=lambda e: (e["confirmed_index"], e["name"]))


def _fit(points: list[tuple[int, float]]) -> tuple[float, float, float]:
    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(((y - pred) ** 2).sum()) / ss_tot if ss_tot else 1.0
    return float(slope), float(intercept), r2


def trendlines(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               atr: Sequence[float | None], as_of: int | None = None, n_points: int = 3,
               left: int = LEFT, right: int = RIGHT, tol_atr: float = 0.25,
               parallel_atr: float = 1.0) -> dict[str, Any]:
    """Least-squares lines through the last `n_points` confirmed swing highs
    (resistance) and swing lows (support), as of bar `as_of`.

    - touches: confirmed swings of that kind since the first fitted point that
      lie within tol_atr × ATR of the line.
    - break_index: first bar after the last fitted pivot was confirmed whose
      close is beyond the line by more than tol_atr × ATR (≤ as_of).
    - channel: both lines exist and their drift apart over the fitted span is
      ≤ parallel_atr × ATR.
    """
    t = len(closes) - 1 if as_of is None else as_of
    a = atr[t]
    ph, pl = confirmed_pivots(highs[:t + 1], lows[:t + 1], left, right)
    out: dict[str, Any] = {"as_of": t, "resistance": None, "support": None, "channel": None}
    if a is None:
        return out
    for key, pts, above in (("resistance", ph, True), ("support", pl, False)):
        if len(pts) < max(n_points, 2):
            continue
        used = pts[-n_points:]
        slope, intercept, r2 = _fit(used)
        touches = sum(1 for i, p in pts if i >= used[0][0]
                      and abs(p - (slope * i + intercept)) <= tol_atr * a)
        brk = None
        for j in range(used[-1][0] + right + 1, t + 1):
            line = slope * j + intercept
            aj = atr[j] or a
            if (closes[j] > line + tol_atr * aj) if above else (closes[j] < line - tol_atr * aj):
                brk = j
                break
        out[key] = {"slope": slope, "intercept": intercept, "r2": r2, "touches": touches,
                    "points": [{"index": i, "price": p} for i, p in used],
                    "value": slope * t + intercept, "broken": brk is not None,
                    "break_index": brk, "confirmed_index": used[-1][0] + right}
    r, s = out["resistance"], out["support"]
    if r and s:
        span = t - min(r["points"][0]["index"], s["points"][0]["index"])
        if abs(r["slope"] - s["slope"]) * span <= parallel_atr * a and r["value"] > s["value"]:
            mean_slope = (r["slope"] + s["slope"]) / 2
            width = r["value"] - s["value"]
            out["channel"] = {
                "upper": r["value"], "lower": s["value"], "width": width,
                "width_atr": width / a, "slope": mean_slope,
                "direction": "UP" if mean_slope > 0.02 * a else "DOWN"
                if mean_slope < -0.02 * a else "FLAT",
                "position": (closes[t] - s["value"]) / width if width else None,
            }
    return out


def range_boundaries(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                     atr: Sequence[float | None], as_of: int | None = None, lookback: int = 60,
                     left: int = LEFT, right: int = RIGHT, tol_atr: float = 0.5,
                     max_width_atr: float = 6.0) -> dict[str, Any] | None:
    """Range from confirmed swings in the last `lookback` bars, as of `as_of`.

    upper = highest swing high, lower = lowest swing low. `is_range` needs ≥ 2
    swing highs within tol_atr × ATR of upper, ≥ 2 swing lows within tol of
    lower, a width ≤ max_width_atr × ATR and the close inside the range.
    """
    t = len(closes) - 1 if as_of is None else as_of
    a = atr[t]
    if a is None:
        return None
    ph, pl = confirmed_pivots(highs[:t + 1], lows[:t + 1], left, right)
    hs = [p for i, p in ph if i >= t - lookback]
    ls = [p for i, p in pl if i >= t - lookback]
    if not hs or not ls:
        return None
    upper, lower = max(hs), min(ls)
    top_touch = sum(1 for p in hs if upper - p <= tol_atr * a)
    bot_touch = sum(1 for p in ls if p - lower <= tol_atr * a)
    width = upper - lower
    is_range = (top_touch >= 2 and bot_touch >= 2 and width <= max_width_atr * a
                and lower <= closes[t] <= upper)
    return {"upper": upper, "lower": lower, "width": width, "width_atr": width / a,
            "upper_touches": top_touch, "lower_touches": bot_touch, "is_range": is_range,
            "position": (closes[t] - lower) / width if width else None, "as_of": t}
