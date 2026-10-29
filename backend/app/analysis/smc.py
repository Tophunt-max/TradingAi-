"""Smart Money Concepts on closed candles, without lookahead.

Swing points are confirmed pivots (they need PIVOT_R later candles), so a
break of structure can only reference swings that were already known at the
breaking candle. Every event carries the index at which it was confirmed.
"""

from __future__ import annotations

from typing import Any

from app.analysis.context import Ctx
from app.indicators.core import confirmed_pivots

PIVOT_L = PIVOT_R = 3


def analyse(ctx: Ctx) -> dict[str, Any]:
    n = len(ctx)
    ph, pl = confirmed_pivots(ctx.h, ctx.l, PIVOT_L, PIVOT_R)
    # Swings become known at index + PIVOT_R.
    known_h = {i + PIVOT_R: (i, p) for i, p in ph}
    known_l = {i + PIVOT_R: (i, p) for i, p in pl}
    events: list[dict[str, Any]] = []
    order_blocks: list[dict[str, Any]] = []
    fvgs: list[dict[str, Any]] = []
    sweeps: list[dict[str, Any]] = []
    displacement: list[dict[str, Any]] = []

    def ev(name: str, direction: str, i: int, level: float | None, detail: str,
           anchor: int | None = None) -> dict[str, Any]:
        return {"name": name, "direction": direction, "index": anchor if anchor is not None
                else i, "confirmed_index": i, "ts": ctx.ts[anchor if anchor is not None else i],
                "confirmed_ts": ctx.ts[i], "level": level, "detail": detail}

    last_h: tuple[int, float] | None = None
    last_l: tuple[int, float] | None = None
    h_broken = l_broken = False
    trend = 0
    for i in range(n):
        if i in known_h:
            last_h, h_broken = known_h[i], False
        if i in known_l:
            last_l, l_broken = known_l[i], False
        a = ctx.s["atr14"][i]
        c, o = ctx.c[i], ctx.o[i]

        # Liquidity sweep: wick through a known swing, close back inside.
        if last_h and not h_broken and ctx.h[i] > last_h[1] and c < last_h[1]:
            sweeps.append(ev("Buy-side liquidity sweep", "BEARISH", i, last_h[1],
                             f"Wick above swing high {last_h[1]:.2f}, closed back below."))
        if last_l and not l_broken and ctx.l[i] < last_l[1] and c > last_l[1]:
            sweeps.append(ev("Sell-side liquidity sweep", "BULLISH", i, last_l[1],
                             f"Wick below swing low {last_l[1]:.2f}, closed back above."))

        # Break of structure / change of character on a CLOSE beyond the swing.
        if last_h and not h_broken and c > last_h[1]:
            name = "CHOCH" if trend == -1 else "BOS"
            events.append(ev(name, "BULLISH", i, last_h[1],
                             f"Close above swing high {last_h[1]:.2f}"
                             + (" against the prior downtrend." if name == "CHOCH" else ".")))
            h_broken, trend = True, 1
            ob = _order_block(ctx, last_h[0], i, bullish=True)
            if ob:
                order_blocks.append(ob)
        if last_l and not l_broken and c < last_l[1]:
            name = "CHOCH" if trend == 1 else "BOS"
            events.append(ev(name, "BEARISH", i, last_l[1],
                             f"Close below swing low {last_l[1]:.2f}"
                             + (" against the prior uptrend." if name == "CHOCH" else ".")))
            l_broken, trend = True, -1
            ob = _order_block(ctx, last_l[0], i, bullish=False)
            if ob:
                order_blocks.append(ob)

        # Fair value gap, confirmed when the third candle closes.
        if i >= 2 and a:
            if ctx.l[i] > ctx.h[i - 2] and ctx.l[i] - ctx.h[i - 2] >= 0.1 * a:
                fvgs.append({**ev("Bullish FVG", "BULLISH", i, None,
                                  f"Gap {ctx.h[i - 2]:.2f}–{ctx.l[i]:.2f}.", anchor=i - 1),
                             "bottom": ctx.h[i - 2], "top": ctx.l[i]})
            elif ctx.h[i] < ctx.l[i - 2] and ctx.l[i - 2] - ctx.h[i] >= 0.1 * a:
                fvgs.append({**ev("Bearish FVG", "BEARISH", i, None,
                                  f"Gap {ctx.h[i]:.2f}–{ctx.l[i - 2]:.2f}.", anchor=i - 1),
                             "bottom": ctx.h[i], "top": ctx.l[i - 2]})

        # Displacement: large, decisive candle.
        rng = ctx.h[i] - ctx.l[i]
        if a and rng and abs(c - o) > 1.5 * a and abs(c - o) / rng > 0.7:
            displacement.append(ev("Displacement", "BULLISH" if c > o else "BEARISH", i, None,
                                   f"Body {abs(c - o):.2f} is {abs(c - o) / a:.1f}× ATR."))

    _mitigate(ctx, fvgs, order_blocks)
    return {
        "structure_events": events,
        "order_blocks": order_blocks,
        "fair_value_gaps": fvgs,
        "liquidity_sweeps": sweeps,
        "equal_levels": _equal_levels(ctx, ph, pl),
        "displacement": displacement,
        "trend": {1: "BULLISH", -1: "BEARISH", 0: "UNDEFINED"}[trend],
    }


def _order_block(ctx: Ctx, swing_i: int, break_i: int, bullish: bool) -> dict[str, Any] | None:
    """Last opposite-colour candle before the move that broke structure."""
    for j in range(break_i - 1, max(swing_i - 10, 0) - 1, -1):
        opposite = ctx.c[j] < ctx.o[j] if bullish else ctx.c[j] > ctx.o[j]
        if opposite:
            return {"name": "Bullish order block" if bullish else "Bearish order block",
                    "direction": "BULLISH" if bullish else "BEARISH", "index": j,
                    "confirmed_index": break_i, "ts": ctx.ts[j], "confirmed_ts": ctx.ts[break_i],
                    "bottom": ctx.l[j], "top": ctx.h[j], "level": None,
                    "detail": f"Last {'bearish' if bullish else 'bullish'} candle before the "
                              f"structure break ({ctx.l[j]:.2f}–{ctx.h[j]:.2f})."}
    return None


def _mitigate(ctx: Ctx, fvgs: list[dict[str, Any]], obs: list[dict[str, Any]]) -> None:
    """Mark zones that price has traded back into after they were confirmed."""
    for z in fvgs + obs:
        z["mitigated"] = False
        z["mitigated_index"] = None
        for j in range(z["confirmed_index"] + 1, len(ctx)):
            touched = (ctx.l[j] <= z["top"]) if z["direction"] == "BULLISH" \
                else (ctx.h[j] >= z["bottom"])
            if touched:
                z["mitigated"], z["mitigated_index"] = True, j
                break


def _equal_levels(ctx: Ctx, ph: list[tuple[int, float]],
                  pl: list[tuple[int, float]]) -> list[dict[str, Any]]:
    out = []
    for pts, name, direction in ((ph, "Equal highs", "BEARISH"), (pl, "Equal lows", "BULLISH")):
        for (i1, p1), (i2, p2) in zip(pts, pts[1:], strict=False):
            a = ctx.s["atr14"][i2]
            if a and abs(p1 - p2) <= 0.1 * a:
                ci = i2 + PIVOT_R
                out.append({"name": name, "direction": direction, "index": i1,
                            "confirmed_index": ci, "ts": ctx.ts[i1], "confirmed_ts": ctx.ts[ci],
                            "level": (p1 + p2) / 2,
                            "detail": f"Two swings at {p1:.2f} and {p2:.2f}: resting liquidity."})
    return out
