"""Candlestick and chart-pattern recognition on CLOSED candles.

Every event has `index` (where the pattern starts or is anchored) and
`confirmed_index` (the first candle at which all required candles exist).
Events are only emitted once confirmed, so adding future candles can never
remove or change an event that was already reported (no repainting).
"""

from __future__ import annotations

from typing import Any

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


def candlestick_patterns(ctx: Ctx) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    o, h, lo, c = ctx.o, ctx.h, ctx.l, ctx.c
    for i in range(len(ctx)):
        rng = h[i] - lo[i]
        if rng <= 0:
            continue
        body = abs(c[i] - o[i])
        upper = h[i] - max(o[i], c[i])
        lower = min(o[i], c[i]) - lo[i]
        trend = _prior_trend(ctx, i)
        if body <= 0.1 * rng:
            out.append(_event(ctx, "Doji", "candlestick", "NEUTRAL", i, i,
                              "Open and close almost equal: indecision."))
        elif lower >= 2 * body and upper <= 0.3 * body + 0.05 * rng and trend == -1:
            out.append(_event(ctx, "Hammer", "candlestick", "BULLISH", i, i,
                              "Long lower wick after a decline: buyers rejected lower prices."))
        elif upper >= 2 * body and lower <= 0.3 * body + 0.05 * rng:
            if trend == -1:
                out.append(_event(ctx, "Inverted hammer", "candlestick", "BULLISH", i, i,
                                  "Long upper wick after a decline: possible reversal, needs "
                                  "confirmation."))
            elif trend == 1:
                out.append(_event(ctx, "Shooting star", "candlestick", "BEARISH", i, i,
                                  "Long upper wick after a rally: sellers rejected higher "
                                  "prices."))
        if i >= 1:
            pb = abs(c[i - 1] - o[i - 1])
            prev_bear, prev_bull = c[i - 1] < o[i - 1], c[i - 1] > o[i - 1]
            if (prev_bear and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1]
                    and body > pb):
                out.append(_event(ctx, "Bullish engulfing", "candlestick", "BULLISH", i - 1, i,
                                  "Bullish body fully covers the previous bearish body."))
            elif (prev_bull and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]
                  and body > pb):
                out.append(_event(ctx, "Bearish engulfing", "candlestick", "BEARISH", i - 1, i,
                                  "Bearish body fully covers the previous bullish body."))
            if h[i] < h[i - 1] and lo[i] > lo[i - 1]:
                out.append(_event(ctx, "Inside bar", "candlestick", "NEUTRAL", i - 1, i,
                                  "Range inside the previous candle: contraction."))
            elif h[i] > h[i - 1] and lo[i] < lo[i - 1]:
                out.append(_event(ctx, "Outside bar", "candlestick", "NEUTRAL", i - 1, i,
                                  "Range engulfs the previous candle: expansion."))
        if i >= 2:
            b1, b2 = abs(c[i - 2] - o[i - 2]), abs(c[i - 1] - o[i - 1])
            r1 = h[i - 2] - lo[i - 2]
            mid1 = (o[i - 2] + c[i - 2]) / 2
            if r1 > 0 and b1 >= 0.6 * r1 and b2 <= 0.3 * b1:
                if c[i - 2] < o[i - 2] and c[i] > o[i] and c[i] > mid1:
                    out.append(_event(ctx, "Morning star", "candlestick", "BULLISH", i - 2, i,
                                      "Strong bearish candle, small pause, bullish close above "
                                      "its midpoint."))
                elif c[i - 2] > o[i - 2] and c[i] < o[i] and c[i] < mid1:
                    out.append(_event(ctx, "Evening star", "candlestick", "BEARISH", i - 2, i,
                                      "Strong bullish candle, small pause, bearish close below "
                                      "its midpoint."))
    return out


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
