"""Candlestick and chart-pattern recognition on CLOSED candles.

Every event has `index` (where the pattern starts or is anchored) and
`confirmed_index` (the first candle at which all required candles exist).
Events are only emitted once confirmed, so adding future candles can never
remove or change an event that was already reported (no repainting).
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from app.analysis import context as C
from app.analysis import regime as R
from app.analysis.context import Ctx
from app.indicators.core import confirmed_pivots

PIVOT_L = PIVOT_R = 3


def _event(ctx: Ctx, name: str, kind: str, direction: str, index: int, confirmed: int,
           detail: str, level: float | None = None) -> dict[str, Any]:
    return {"name": name, "kind": kind, "direction": direction, "index": index,
            "confirmed_index": confirmed, "ts": ctx.ts[index],
            "confirmed_ts": ctx.ts[confirmed], "detail": detail, "level": level}


def _atr(ctx: Ctx, i: int) -> float | None:
    return ctx.s["atr14"][i]


def _prior_trend(ctx: Ctx, i: int, n: int = 5) -> int:
    """+1 if the n closes before i were rising, -1 if falling, 0 otherwise."""
    if i - n < 0:
        return 0
    move = ctx.c[i - 1] - ctx.c[i - n]
    a = _atr(ctx, i - 1)
    if a is None or abs(move) < 0.5 * a:
        return 0
    return 1 if move > 0 else -1


# Shape thresholds (fractions of the candle range unless noted). See
# docs/technical-analysis.md for the definitions that use them.
THRESHOLDS: dict[str, float] = {
    "doji_body": 0.10,          # body ≤ 10% of range
    "doji_short_wick": 0.10,    # dragonfly / gravestone: the short wick ≤ 10% of range
    "doji_long_wick": 0.60,     # dragonfly / gravestone: the long wick ≥ 60% of range
    "long_legged_wick": 0.30,   # both wicks ≥ 30% of range ...
    "long_legged_atr": 1.0,     # ... and range ≥ 1 × ATR(14)
    "hammer_wick_body": 2.0,    # long wick ≥ 2 × body
    "marubozu_body": 0.90,      # body ≥ 90% of range
    "spinning_body": 0.30,      # body ≤ 30% of range ...
    "spinning_wick": 0.25,      # ... and each wick ≥ 25% of range
    "strong_body": 0.50,        # "strong" first candle: body ≥ 50% of range
    "harami_body": 0.60,        # harami second body ≤ 60% of the first body
    "tweezer_atr": 0.10,        # tweezer extremes within 0.1 × ATR(14)
    "soldier_body": 0.60,       # soldiers / crows: body ≥ 60% of range ...
    "soldier_wick": 0.25,       # ... and closing-side wick ≤ 25% of range
}
CONFIRM_BARS = 3  # confirmation must happen within this many bars after detection
TREND = {1: "UP", -1: "DOWN", 0: "FLAT"}


def _doji(ctx: Ctx, i: int, rng: float, upper: float, lower: float,
          trend: int) -> tuple[str, str, str]:
    t = THRESHOLDS
    a = _atr(ctx, i)
    if upper <= t["doji_short_wick"] * rng and lower >= t["doji_long_wick"] * rng:
        return ("Dragonfly doji", "BULLISH" if trend == -1 else "NEUTRAL",
                "Open and close at the high after a long lower wick: lower prices rejected.")
    if lower <= t["doji_short_wick"] * rng and upper >= t["doji_long_wick"] * rng:
        return ("Gravestone doji", "BEARISH" if trend == 1 else "NEUTRAL",
                "Open and close at the low after a long upper wick: higher prices rejected.")
    if (upper >= t["long_legged_wick"] * rng and lower >= t["long_legged_wick"] * rng
            and a is not None and rng >= t["long_legged_atr"] * a):
        return ("Long-legged doji", "NEUTRAL",
                "Wide range with long wicks both sides and almost no body: strong indecision.")
    return "Doji", "NEUTRAL", "Open and close almost equal: indecision."


def candlestick_patterns(ctx: Ctx) -> list[dict[str, Any]]:
    """Raw candlestick events, each enriched with context and confirmation."""
    out: list[dict[str, Any]] = []
    t = THRESHOLDS
    o, h, lo, c = ctx.o, ctx.h, ctx.l, ctx.c

    def add(*args: Any) -> None:
        out.append(_event(ctx, *args))

    for i in range(len(ctx)):
        rng = h[i] - lo[i]
        if rng <= 0:
            continue
        body = abs(c[i] - o[i])
        upper = h[i] - max(o[i], c[i])
        lower = min(o[i], c[i]) - lo[i]
        bull, bear = c[i] > o[i], c[i] < o[i]
        trend = _prior_trend(ctx, i)
        small_wick = 0.3 * body + 0.05 * rng
        if body <= t["doji_body"] * rng:
            name, direction, detail = _doji(ctx, i, rng, upper, lower, trend)
            add(name, "candlestick", direction, i, i, detail)
        elif lower >= t["hammer_wick_body"] * body and upper <= small_wick:
            if trend == -1:
                add("Hammer", "candlestick", "BULLISH", i, i,
                    "Long lower wick after a decline: buyers rejected lower prices.")
            elif trend == 1:
                add("Hanging man", "candlestick", "BEARISH", i, i,
                    "Hammer shape after a rally: selling appeared below; needs confirmation.")
        elif upper >= t["hammer_wick_body"] * body and lower <= small_wick:
            if trend == -1:
                add("Inverted hammer", "candlestick", "BULLISH", i, i,
                    "Long upper wick after a decline: possible reversal, needs confirmation.")
            elif trend == 1:
                add("Shooting star", "candlestick", "BEARISH", i, i,
                    "Long upper wick after a rally: sellers rejected higher prices.")
        elif body >= t["marubozu_body"] * rng:
            add("Bullish marubozu" if bull else "Bearish marubozu", "candlestick",
                "BULLISH" if bull else "BEARISH", i, i,
                "Body fills almost the whole range: one side controlled the candle.")
        elif (body <= t["spinning_body"] * rng and upper >= t["spinning_wick"] * rng
              and lower >= t["spinning_wick"] * rng):
            add("Spinning top", "candlestick", "NEUTRAL", i, i,
                "Small body with wicks both sides: indecision.")
        if i >= 1:
            po, pc, ph_, pl_ = o[i - 1], c[i - 1], h[i - 1], lo[i - 1]
            pb, pr = abs(pc - po), ph_ - pl_
            prev_bear, prev_bull = pc < po, pc > po
            strong_prev = pr > 0 and pb >= t["strong_body"] * pr
            t2 = _prior_trend(ctx, i - 1)
            mid = (po + pc) / 2
            a = _atr(ctx, i)
            if prev_bear and bull and o[i] <= pc and c[i] >= po and body > pb:
                add("Bullish engulfing", "candlestick", "BULLISH", i - 1, i,
                    "Bullish body fully covers the previous bearish body.")
            elif prev_bull and bear and o[i] >= pc and c[i] <= po and body > pb:
                add("Bearish engulfing", "candlestick", "BEARISH", i - 1, i,
                    "Bearish body fully covers the previous bullish body.")
            elif (prev_bear and strong_prev and bull and o[i] <= pc and mid < c[i] < po
                  and t2 == -1):
                add("Piercing line", "candlestick", "BULLISH", i - 1, i,
                    "After a strong bearish candle, a bullish candle closes above its midpoint.")
            elif (prev_bull and strong_prev and bear and o[i] >= pc and po < c[i] < mid
                  and t2 == 1):
                add("Dark cloud cover", "candlestick", "BEARISH", i - 1, i,
                    "After a strong bullish candle, a bearish candle closes below its midpoint.")
            elif (prev_bear and strong_prev and bull and o[i] > pc and c[i] < po
                  and body <= t["harami_body"] * pb and t2 == -1):
                add("Bullish harami", "candlestick", "BULLISH", i - 1, i,
                    "Small bullish body inside the previous large bearish body.")
            elif (prev_bull and strong_prev and bear and o[i] < pc and c[i] > po
                  and body <= t["harami_body"] * pb and t2 == 1):
                add("Bearish harami", "candlestick", "BEARISH", i - 1, i,
                    "Small bearish body inside the previous large bullish body.")
            if a is not None and prev_bear and bull and t2 == -1 \
                    and abs(lo[i] - pl_) <= t["tweezer_atr"] * a:
                add("Tweezer bottom", "candlestick", "BULLISH", i - 1, i,
                    "Two candles with matching lows after a decline: the low held twice.")
            elif a is not None and prev_bull and bear and t2 == 1 \
                    and abs(h[i] - ph_) <= t["tweezer_atr"] * a:
                add("Tweezer top", "candlestick", "BEARISH", i - 1, i,
                    "Two candles with matching highs after a rally: the high held twice.")
            if h[i] < ph_ and lo[i] > pl_:
                add("Inside bar", "candlestick", "NEUTRAL", i - 1, i,
                    "Range inside the previous candle: contraction.")
            elif h[i] > ph_ and lo[i] < pl_:
                add("Outside bar", "candlestick", "NEUTRAL", i - 1, i,
                    "Range engulfs the previous candle: expansion.")
        if i >= 2:
            b1, b2 = abs(c[i - 2] - o[i - 2]), abs(c[i - 1] - o[i - 1])
            r1 = h[i - 2] - lo[i - 2]
            mid1 = (o[i - 2] + c[i - 2]) / 2
            if r1 > 0 and b1 >= 0.6 * r1 and b2 <= 0.3 * b1:
                if c[i - 2] < o[i - 2] and c[i] > o[i] and c[i] > mid1:
                    add("Morning star", "candlestick", "BULLISH", i - 2, i,
                        "Strong bearish candle, small pause, bullish close above its midpoint.")
                elif c[i - 2] > o[i - 2] and c[i] < o[i] and c[i] < mid1:
                    add("Evening star", "candlestick", "BEARISH", i - 2, i,
                        "Strong bullish candle, small pause, bearish close below its midpoint.")
            out.extend(_three_bar(ctx, i))
    for e in out:
        _enrich(ctx, e)
    return out


def _three_bar(ctx: Ctx, i: int) -> list[dict[str, Any]]:
    t = THRESHOLDS
    o, h, lo, c = ctx.o, ctx.h, ctx.l, ctx.c
    idx = (i - 2, i - 1, i)
    out: list[dict[str, Any]] = []

    def soldier(j: int, up: bool) -> bool:
        r = h[j] - lo[j]
        if r <= 0 or abs(c[j] - o[j]) < t["soldier_body"] * r:
            return False
        wick = h[j] - c[j] if up else c[j] - lo[j]
        return (c[j] > o[j] if up else c[j] < o[j]) and wick <= t["soldier_wick"] * r

    if all(soldier(j, True) for j in idx) and c[i - 2] < c[i - 1] < c[i] \
            and all(o[j - 1] <= o[j] <= c[j - 1] for j in idx[1:]):
        out.append(_event(ctx, "Three white soldiers", "candlestick", "BULLISH", i - 2, i,
                          "Three strong bullish candles, each opening inside the prior body "
                          "and closing higher."))
    elif all(soldier(j, False) for j in idx) and c[i - 2] > c[i - 1] > c[i] \
            and all(c[j - 1] <= o[j] <= o[j - 1] for j in idx[1:]):
        out.append(_event(ctx, "Three black crows", "candlestick", "BEARISH", i - 2, i,
                          "Three strong bearish candles, each opening inside the prior body "
                          "and closing lower."))
    po, pc, pb, pr = o[i - 2], c[i - 2], abs(c[i - 2] - o[i - 2]), h[i - 2] - lo[i - 2]
    b2 = abs(c[i - 1] - o[i - 1])
    strong = pr > 0 and pb >= t["strong_body"] * pr and b2 <= t["harami_body"] * pb
    trend = _prior_trend(ctx, i - 2)
    if (strong and pc < po and c[i - 1] > o[i - 1] and o[i - 1] > pc and c[i - 1] < po
            and c[i] > o[i] and c[i] > po and trend == -1):
        out.append(_event(ctx, "Three inside up", "candlestick", "BULLISH", i - 2, i,
                          "Bullish harami followed by a close above the first candle's open."))
    elif (strong and pc > po and c[i - 1] < o[i - 1] and o[i - 1] < pc and c[i - 1] > po
          and c[i] < o[i] and c[i] < po and trend == 1):
        out.append(_event(ctx, "Three inside down", "candlestick", "BEARISH", i - 2, i,
                          "Bearish harami followed by a close below the first candle's open."))
    t1 = _prior_trend(ctx, i - 1)
    if lo[i - 1] < lo[i - 2] and lo[i - 1] < lo[i] and c[i] > h[i - 1] and c[i] > o[i] \
            and t1 == -1:
        out.append(_event(ctx, "Bullish three-bar reversal", "candlestick", "BULLISH", i - 2, i,
                          "Middle candle makes the lowest low; the third closes above its high."))
    elif h[i - 1] > h[i - 2] and h[i - 1] > h[i] and c[i] < lo[i - 1] and c[i] < o[i] \
            and t1 == 1:
        out.append(_event(ctx, "Bearish three-bar reversal", "candlestick", "BEARISH", i - 2, i,
                          "Middle candle makes the highest high; the third closes below its "
                          "low."))
    return out


def _r4(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def _context(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    """Pattern context measured at the detection bar (no later candles)."""
    i0, f = e["index"], e["detection_index"]
    rng = ctx.h[f] - ctx.l[f]
    body = abs(ctx.c[f] - ctx.o[f])
    a = _atr(ctx, f)
    c = ctx.c[f]
    ph, pl = _pivot_cache(ctx)
    res = min((p for i, p in ph if i + PIVOT_R <= f and p > c), default=None)
    sup = max((p for i, p in pl if i + PIVOT_R <= f and p < c), default=None)
    reg = R.detect(ctx, f)
    return {
        "body_range_ratio": _r4(body / rng) if rng else 0.0,
        "upper_wick_ratio": _r4((ctx.h[f] - max(ctx.o[f], c)) / rng) if rng else 0.0,
        "lower_wick_ratio": _r4((min(ctx.o[f], c) - ctx.l[f]) / rng) if rng else 0.0,
        "close_location": _r4((c - ctx.l[f]) / rng) if rng else 0.5,
        "relative_volume": _r4(ctx.s["rvol20"][f]),
        "prior_trend": TREND[_prior_trend(ctx, i0)],
        "rsi14": _r4(ctx.s["rsi14"][f]),
        "roc12": _r4(ctx.s["roc12"][f]),
        "nearest_resistance": _r4(res),
        "resistance_distance_atr": _r4((res - c) / a) if res is not None and a else None,
        "nearest_support": _r4(sup),
        "support_distance_atr": _r4((c - sup) / a) if sup is not None and a else None,
        "volatility_state": ctx.s["vol_state"][f],
        "regime": reg["label"] if reg else None,
        "atr14": _r4(a),
    }


def _pivot_cache(ctx: Ctx) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    if "pivots" not in ctx.cache:
        ctx.cache["pivots"] = pivots(ctx)
    ph, pl = ctx.cache["pivots"]
    return ph, pl


def _enrich(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    """Add detection/confirmation/invalidation and context to an event.

    detection_index = the bar on which the pattern is complete (equal to the
    legacy `confirmed_index`). For candlesticks, confirmation requires a later
    close beyond the pattern high (bullish) or low (bearish) within
    CONFIRM_BARS bars; a close beyond the other extreme first invalidates it.
    Chart patterns are already confirmed by their neckline/level break.
    """
    n = len(ctx)
    i0, f = e["index"], e["confirmed_index"]
    e["detection_index"], e["detection_ts"] = f, ctx.ts[f]
    hi, lo = max(ctx.h[i0:f + 1]), min(ctx.l[i0:f + 1])
    e["pattern_high"], e["pattern_low"] = _r4(hi), _r4(lo)
    d = e["direction"]
    conf: dict[str, Any] = {"window_bars": CONFIRM_BARS, "status": "PENDING", "index": None,
                            "ts": None, "direction": None if d == "NEUTRAL" else d}
    if e["kind"] == "chart":
        conf.update(rule="Close through the neckline / reference level (already required).",
                    status="CONFIRMED", index=f, ts=ctx.ts[f], window_bars=0)
        lvl = e.get("level")
        e["invalidation"] = {
            "level": _r4(lvl),
            "rule": "A close back " + ("below" if d == "BULLISH" else "above")
                    + " the broken level." if lvl is not None else "n/a"}
    else:
        conf["rule"] = (f"A close above the pattern high {hi:.2f} within {CONFIRM_BARS} bars"
                        if d == "BULLISH" else
                        f"A close below the pattern low {lo:.2f} within {CONFIRM_BARS} bars"
                        if d == "BEARISH" else
                        f"A close beyond the pattern high {hi:.2f} or low {lo:.2f} within "
                        f"{CONFIRM_BARS} bars; the break sets the direction")
        for j in range(f + 1, min(f + CONFIRM_BARS, n - 1) + 1):
            up, dn = ctx.c[j] > hi, ctx.c[j] < lo
            if not (up or dn):
                continue
            if d == "NEUTRAL" or (d == "BULLISH" and up) or (d == "BEARISH" and dn):
                conf.update(status="CONFIRMED", index=j, ts=ctx.ts[j],
                            direction="BULLISH" if up else "BEARISH")
            else:
                conf.update(status="INVALIDATED", index=j, ts=ctx.ts[j])
            break
        else:
            if f + CONFIRM_BARS <= n - 1:
                conf["status"] = "EXPIRED"
        cd = conf["direction"]
        e["invalidation"] = (
            {"level": _r4(lo), "rule": f"A close below the pattern low {lo:.2f}."}
            if cd == "BULLISH" else
            {"level": _r4(hi), "rule": f"A close above the pattern high {hi:.2f}."}
            if cd == "BEARISH" else
            {"level": None, "rule": "Set once a break of the pattern high or low gives a "
                                    "direction; the opposite extreme then invalidates it."})
    e["confirmation"] = conf
    e["confirmed"] = conf["status"] == "CONFIRMED"
    e["context"] = _context(ctx, e)
    return e


def pivots(ctx: Ctx) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    return confirmed_pivots(ctx.h, ctx.l, PIVOT_L, PIVOT_R)


def _first_close(ctx: Ctx, start: int, level: float, above: bool) -> int | None:
    """First candle from `start` that closes above (or below) `level`."""
    for j in range(start, len(ctx)):
        if (ctx.c[j] > level) if above else (ctx.c[j] < level):
            return j
    return None


def chart_patterns(ctx: Ctx) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ph, pl = pivots(ctx)
    swings = sorted([(i, p, "H") for i, p in ph] + [(i, p, "L") for i, p in pl])

    # Double top / bottom: two same-type pivots, similar level, a pivot between,
    # confirmed by a close through the neckline.
    for kind, name, direction in (("H", "Double top", "BEARISH"),
                                  ("L", "Double bottom", "BULLISH")):
        pts = [(i, p) for i, p, k in swings if k == kind]
        for (i1, p1), (i2, p2) in zip(pts, pts[1:], strict=False):
            a = _atr(ctx, i2)
            if a is None or i2 - i1 < 5 or abs(p1 - p2) > 0.5 * a:
                continue
            between = [p for i, p, k in swings if i1 < i < i2 and k != kind]
            if not between:
                continue
            neck = min(between) if kind == "H" else max(between)
            start = i2 + PIVOT_R
            j = _first_close(ctx, start, neck, above=kind == "L")
            if j is not None and j - i2 <= 30:
                out.append(_event(ctx, name, "chart", direction, i1, j,
                                  f"Two {'peaks' if kind == 'H' else 'troughs'} near "
                                  f"{p2:.2f}; neckline {neck:.2f} broken.", neck))

    # Head and shoulders (and inverse): three same-type pivots, middle extreme.
    for kind, name, direction in (("H", "Head and shoulders", "BEARISH"),
                                  ("L", "Inverse head and shoulders", "BULLISH")):
        pts = [(i, p) for i, p, k in swings if k == kind]
        for (i1, p1), (_i2, p2), (i3, p3) in zip(pts, pts[1:], pts[2:], strict=False):
            a = _atr(ctx, i3)
            if a is None:
                continue
            head_ok = (p2 - max(p1, p3) > 0.5 * a) if kind == "H" else (min(p1, p3) - p2 > 0.5 * a)
            if not head_ok or abs(p1 - p3) > 1.0 * a:
                continue
            troughs = [p for i, p, k in swings if i1 < i < i3 and k != kind]
            if len(troughs) < 2:
                continue
            neck = min(troughs) if kind == "H" else max(troughs)
            j = _first_close(ctx, i3 + PIVOT_R, neck, above=kind == "L")
            if j is not None and j - i3 <= 30:
                out.append(_event(ctx, name, "chart", direction, i1, j,
                                  f"Head {p2:.2f} between shoulders {p1:.2f} / {p3:.2f}; "
                                  f"neckline {neck:.2f} broken.", neck))

    # Breakout and retest of the most recent confirmed swing level.
    for kind, name, direction in (("H", "Breakout and retest", "BULLISH"),
                                  ("L", "Breakdown and retest", "BEARISH")):
        pts = [(i, p) for i, p, k in swings if k == kind]
        for i0, level in pts:
            start = i0 + PIVOT_R
            brk = _first_close(ctx, start, level, above=kind == "H")
            if brk is None or brk - start > 40:
                continue
            for j in range(brk + 1, min(brk + 11, len(ctx))):
                a = _atr(ctx, j)
                if a is None:
                    break
                if kind == "H":
                    touched, held = ctx.l[j] <= level + 0.25 * a, ctx.c[j] > level
                else:
                    touched, held = ctx.h[j] >= level - 0.25 * a, ctx.c[j] < level
                if touched and held:
                    out.append(_event(ctx, name, "chart", direction, i0, j,
                                      f"Closed beyond {level:.2f}, then retested it and held.",
                                      level))
                    break
                if not held:
                    break

    # Flags / pennants: strong impulse, tight consolidation, break in impulse direction.
    # Each candidate depends only on candles up to its breakout, and at most one
    # flag is kept per breakout candle (earliest impulse), so results never
    # change when later candles arrive.
    n = len(ctx)
    seen_breakouts: set[int] = set()
    for i in range(5, n - 6):
        a = _atr(ctx, i)
        move = ctx.c[i] - ctx.c[i - 5]
        if a is None or abs(move) < 3 * a:
            continue
        up = move > 0
        for length in range(5, 16):
            end = i + length
            if end >= n:
                break
            hi, lo = max(ctx.h[i + 1:end + 1]), min(ctx.l[i + 1:end + 1])
            if hi - lo > 0.5 * abs(move):
                break
            nxt = end + 1
            if nxt < n and ((up and ctx.c[nxt] > hi) or (not up and ctx.c[nxt] < lo)):
                if nxt not in seen_breakouts:
                    seen_breakouts.add(nxt)
                    out.append(_event(ctx, "Bull flag" if up else "Bear flag", "chart",
                                      "BULLISH" if up else "BEARISH", i - 5, nxt,
                                      f"Impulse of {abs(move):.2f}, {length}-bar consolidation, "
                                      "breakout in the impulse direction.", hi if up else lo))
                break
    for e in out:
        _enrich(ctx, e)
    return out


def structure(ctx: Ctx) -> dict[str, Any] | None:
    """Market structure from the last two confirmed swing highs and lows."""
    ph, pl = pivots(ctx)
    if len(ph) < 2 or len(pl) < 2:
        return None
    (_, h1), (ih2, h2) = ph[-2], ph[-1]
    (_, l1), (il2, l2) = pl[-2], pl[-1]
    hh, hl = h2 > h1, l2 > l1
    if hh and hl:
        label, direction = "Higher highs & higher lows", "BULLISH"
    elif not hh and not hl:
        label, direction = "Lower highs & lower lows", "BEARISH"
    else:
        label, direction = "Mixed structure", "NEUTRAL"
    confirmed = max(ih2, il2) + PIVOT_R
    return {"label": label, "direction": direction, "swing_highs": [h1, h2],
            "swing_lows": [l1, l2], "confirmed_ts": ctx.ts[confirmed]}


def all_patterns(ctx: Ctx) -> list[dict[str, Any]]:
    events = candlestick_patterns(ctx) + chart_patterns(ctx)
    return sorted(events, key=lambda e: (e["confirmed_index"], e["name"]))


# ------------------------------------------------------- outcome statistics

MIN_SAMPLES = 30


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _summary(returns: list[float], min_samples: int) -> dict[str, Any]:
    n = len(returns)
    hits = sum(1 for r in returns if r > 0)
    ci = wilson_interval(hits, n)
    return {
        "n": n,
        "mean_net_return_pct": round(100 * statistics.fmean(returns), 4) if n else None,
        "median_net_return_pct": round(100 * statistics.median(returns), 4) if n else None,
        "stdev_net_return_pct": round(100 * statistics.stdev(returns), 4) if n > 1 else None,
        "hit_rate": round(hits / n, 4) if n else None,
        "hit_rate_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "insufficient_sample": n < min_samples,
    }


def outcome_definition(horizon: int, cost_bps: float) -> str:
    return (f"For each CONFIRMED candlestick pattern: enter at the OPEN of the bar after the "
            f"confirmation bar, exit at the CLOSE {horizon} bars after the confirmation bar, "
            f"in the pattern's direction (short for bearish). Net return = gross return − "
            f"{cost_bps:g} bps round-trip cost. A hit is a net return > 0. Hit-rate interval: "
            "Wilson 95%.")


def pattern_outcome_stats(candles: list[dict[str, Any]], horizon: int = 5,
                          cost_bps: float = 5.0, by_regime: bool = True,
                          min_samples: int = MIN_SAMPLES, timeframe: str | None = None
                          ) -> dict[str, Any]:
    """Chronological forward-outcome statistics of confirmed candlestick patterns.

    Only patterns whose confirmation bar exists are used; the entry is the next
    bar's open so nothing at or before the decision uses later prices. Samples
    whose exit bar is beyond the data are dropped. Overlapping windows are not
    independent, so the interval is optimistic when patterns cluster.
    """
    if horizon < 1:
        raise ValueError("horizon must be ≥ 1")
    ctx = C.build(candles, intraday=timeframe != "1d", timeframe=timeframe)
    n = len(ctx)
    cost = cost_bps / 1e4
    groups: dict[str, list[float]] = {}
    regimes: dict[str, dict[str, list[float]]] = {}
    meta: dict[str, dict[str, Any]] = {}
    events = sorted(candlestick_patterns(ctx), key=lambda e: e["detection_index"])
    for e in events:
        conf = e["confirmation"]
        if conf["status"] != "CONFIRMED":
            continue
        j = conf["index"]
        if j + horizon >= n or j + 1 >= n:
            continue
        direction = conf["direction"]
        side = 1 if direction == "BULLISH" else -1
        entry, exit_ = ctx.o[j + 1], ctx.c[j + horizon]
        net = side * (exit_ / entry - 1) - cost
        key = e["name"] if e["direction"] != "NEUTRAL" else f"{e['name']} ({direction} break)"
        groups.setdefault(key, []).append(net)
        meta.setdefault(key, {"pattern": e["name"], "direction": direction})
        if by_regime:
            reg = R.detect(ctx, j)
            regimes.setdefault(key, {}).setdefault(reg["label"] if reg else "UNKNOWN",
                                                   []).append(net)
    rows = []
    for key in sorted(groups):
        row = {"key": key, **meta[key], **_summary(groups[key], min_samples)}
        if by_regime:
            row["by_regime"] = {r: _summary(v, min_samples) for r, v in
                                sorted(regimes[key].items())}
        rows.append(row)
    longs = [ctx.c[j + horizon] / ctx.o[j + 1] - 1 - cost for j in range(n - horizon - 1)]
    shorts = [-(ctx.c[j + horizon] / ctx.o[j + 1] - 1) - cost for j in range(n - horizon - 1)]
    return {
        "outcome_definition": outcome_definition(horizon, cost_bps),
        "horizon_bars": horizon, "cost_bps": cost_bps, "min_samples": min_samples,
        "candles": n, "first_ts": ctx.ts[0] if n else None, "last_ts": ctx.ts[-1] if n else None,
        "patterns": rows,
        "baseline": {"description": "Every bar, same entry/exit rule, regardless of patterns.",
                     "long": _summary(longs, min_samples),
                     "short": _summary(shorts, min_samples)},
        "limitations": [
            "Underlying price only: option premiums, theta, IV and spreads are not modelled.",
            "Overlapping forward windows are not independent; intervals are optimistic.",
            "One fixed cost per round trip; real slippage varies with liquidity.",
            "Past frequencies on this window do not imply future results.",
        ],
    }
