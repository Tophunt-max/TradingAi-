"""Price structure ("Smart Money Concepts") on closed candles, without lookahead.

These are deterministic price-structure heuristics. They describe where
price broke, gapped or reversed; they are NOT evidence of institutional
activity, and the names are conventional labels only.

Swing points are confirmed pivots (they need `pivot_right` later candles), so
a break of structure can only reference swings that were already known at the
breaking candle. Every event carries:

- index / ts: the anchor candle (e.g. the order-block candle, first base bar);
- detected_index / detected_ts: the bar on which the event's condition occurred;
- confirmed_index / confirmed_ts: the first bar at which it is known (for
  pivot-based events detected + pivot_right, otherwise equal to detected);
- reference: the swing or level the event refers to;
- invalidation: {level, rule};
- data_quality: the market-data quality status of the window.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.analysis.context import Ctx
from app.indicators.core import confirmed_pivots
from app.indicators.structure import swing_points

PIVOT_L = PIVOT_R = 3
DISCLAIMER = ("Price-structure heuristics with fixed, documented thresholds. They are not "
              "evidence of institutional (\"smart money\") activity.")


@dataclass(frozen=True)
class SMCConfig:
    pivot_left: int = PIVOT_L
    pivot_right: int = PIVOT_R
    fvg_min_atr: float = 0.1             # gap ≥ 0.1 × ATR(14)
    displacement_body_atr: float = 1.5   # body > 1.5 × ATR(14) ...
    displacement_body_range: float = 0.7  # ... and body > 70% of range
    equal_level_atr: float = 0.1         # two swings within 0.1 × ATR
    order_block_lookback: int = 10       # bars before the broken swing to search
    retest_window: int = 10              # bars after a break to look for a retest
    retest_tol_atr: float = 0.25         # retest = wick within 0.25 × ATR of the level
    failed_breakout_window: int = 5      # close back through the level within 5 bars
    base_max_candles: int = 3            # supply/demand base: up to 3 candles ...
    base_body_range: float = 0.5         # ... each with body ≤ 50% of range
    equilibrium_band: float = 0.05       # ±5% of the swing range around the midpoint


DEFAULT = SMCConfig()


def analyse(ctx: Ctx, cfg: SMCConfig | None = None, quality: str | None = None
            ) -> dict[str, Any]:
    cfg = cfg or DEFAULT
    dq = quality or "NOT_CHECKED"
    n = len(ctx)
    R_ = cfg.pivot_right
    ph, pl = confirmed_pivots(ctx.h, ctx.l, cfg.pivot_left, R_)
    # Swings become known at index + pivot_right.
    known_h = {i + R_: (i, p) for i, p in ph}
    known_l = {i + R_: (i, p) for i, p in pl}
    events: list[dict[str, Any]] = []
    order_blocks: list[dict[str, Any]] = []
    fvgs: list[dict[str, Any]] = []
    sweeps: list[dict[str, Any]] = []
    displacement: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []

    def swing_ref(kind: str, sw: tuple[int, float]) -> dict[str, Any]:
        return {"kind": kind, "index": sw[0], "ts": ctx.ts[sw[0]], "price": sw[1],
                "confirmed_ts": ctx.ts[sw[0] + R_]}

    def ev(name: str, direction: str, i: int, level: float | None, detail: str,
           anchor: int | None = None, *, reference: dict[str, Any] | None = None,
           inval: tuple[float | None, str] = (None, "n/a"),
           detected: int | None = None) -> dict[str, Any]:
        a_i = anchor if anchor is not None else i
        d_i = detected if detected is not None else i
        return {"name": name, "direction": direction, "index": a_i, "confirmed_index": i,
                "ts": ctx.ts[a_i], "confirmed_ts": ctx.ts[i], "detected_index": d_i,
                "detected_ts": ctx.ts[d_i], "level": level, "detail": detail,
                "reference": reference,
                "invalidation": {"level": inval[0], "rule": inval[1]}, "data_quality": dq}

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
                             f"Wick above swing high {last_h[1]:.2f}, closed back below.",
                             reference=swing_ref("swing_high", last_h),
                             inval=(ctx.h[i], f"A close above the sweep high {ctx.h[i]:.2f} "
                                              "means the level was taken, not swept.")))
        if last_l and not l_broken and ctx.l[i] < last_l[1] and c > last_l[1]:
            sweeps.append(ev("Sell-side liquidity sweep", "BULLISH", i, last_l[1],
                             f"Wick below swing low {last_l[1]:.2f}, closed back above.",
                             reference=swing_ref("swing_low", last_l),
                             inval=(ctx.l[i], f"A close below the sweep low {ctx.l[i]:.2f} "
                                              "means the level was taken, not swept.")))

        # Break of structure / change of character on a CLOSE beyond the swing.
        if last_h and not h_broken and c > last_h[1]:
            name = "CHOCH" if trend == -1 else "BOS"
            stop = last_l[1] if last_l else ctx.l[i]
            events.append(ev(name, "BULLISH", i, last_h[1],
                             f"Close above swing high {last_h[1]:.2f}"
                             + (" against the prior downtrend." if name == "CHOCH" else "."),
                             reference=swing_ref("swing_high", last_h),
                             inval=(stop, f"A close below {stop:.2f} (last confirmed swing low "
                                          "at the break) negates the bullish structure.")))
            h_broken, trend = True, 1
            ob = _order_block(ctx, last_h, i, True, cfg, dq)
            if ob:
                order_blocks.append(ob)
        if last_l and not l_broken and c < last_l[1]:
            name = "CHOCH" if trend == 1 else "BOS"
            stop = last_h[1] if last_h else ctx.h[i]
            events.append(ev(name, "BEARISH", i, last_l[1],
                             f"Close below swing low {last_l[1]:.2f}"
                             + (" against the prior uptrend." if name == "CHOCH" else "."),
                             reference=swing_ref("swing_low", last_l),
                             inval=(stop, f"A close above {stop:.2f} (last confirmed swing high "
                                          "at the break) negates the bearish structure.")))
            l_broken, trend = True, -1
            ob = _order_block(ctx, last_l, i, False, cfg, dq)
            if ob:
                order_blocks.append(ob)

        # Fair value gap, confirmed when the third candle closes.
        if i >= 2 and a:
            gap_ref = {"kind": "three_candle_gap", "index": i - 2, "ts": ctx.ts[i - 2],
                       "price": None}
            if ctx.l[i] > ctx.h[i - 2] and ctx.l[i] - ctx.h[i - 2] >= cfg.fvg_min_atr * a:
                b_, t_ = ctx.h[i - 2], ctx.l[i]
                fvgs.append({**ev("Bullish FVG", "BULLISH", i, None,
                                  f"Gap {b_:.2f}–{t_:.2f}.", anchor=i - 1, reference=gap_ref,
                                  inval=(b_, f"A close below the gap bottom {b_:.2f} (gap "
                                             "fully filled).")),
                             "bottom": b_, "top": t_})
            elif ctx.h[i] < ctx.l[i - 2] and ctx.l[i - 2] - ctx.h[i] >= cfg.fvg_min_atr * a:
                b_, t_ = ctx.h[i], ctx.l[i - 2]
                fvgs.append({**ev("Bearish FVG", "BEARISH", i, None,
                                  f"Gap {b_:.2f}–{t_:.2f}.", anchor=i - 1, reference=gap_ref,
                                  inval=(t_, f"A close above the gap top {t_:.2f} (gap fully "
                                             "filled).")),
                             "bottom": b_, "top": t_})

        # Displacement: large, decisive candle; its base becomes a supply/demand zone.
        rng = ctx.h[i] - ctx.l[i]
        body = abs(c - o)
        if a and rng and body > cfg.displacement_body_atr * a \
                and body / rng > cfg.displacement_body_range:
            up = c > o
            lvl = ctx.l[i] if up else ctx.h[i]
            displacement.append(ev("Displacement", "BULLISH" if up else "BEARISH", i, None,
                                   f"Body {body:.2f} is {body / a:.1f}× ATR.",
                                   inval=(lvl, f"A close {'below' if up else 'above'} the "
                                               f"displacement candle's "
                                               f"{'low' if up else 'high'} {lvl:.2f}.")))
            z = _base_zone(ctx, i, up, cfg, dq)
            if z:
                zones.append(z)

    _mitigate(ctx, fvgs, order_blocks + zones)
    retests, failed = _breakout_followups(ctx, events, cfg, dq)
    swings = _swings(ctx, cfg, dq)
    return {
        "structure_events": events,
        "order_blocks": order_blocks,
        "fair_value_gaps": fvgs,
        "liquidity_sweeps": sweeps,
        "equal_levels": _equal_levels(ctx, ph, pl, cfg, dq),
        "displacement": displacement,
        "supply_demand": zones,
        "breakout_retests": retests,
        "failed_breakouts": failed,
        "swings": swings,
        "premium_discount": premium_discount(ctx, cfg, dq),
        "trend": {1: "BULLISH", -1: "BEARISH", 0: "UNDEFINED"}[trend],
        "thresholds": asdict(cfg),
        "data_quality": dq,
        "disclaimer": DISCLAIMER,
    }


def _order_block(ctx: Ctx, swing: tuple[int, float], break_i: int, bullish: bool,
                 cfg: SMCConfig, dq: str) -> dict[str, Any] | None:
    """Last opposite-colour candle before the move that broke structure."""
    swing_i = swing[0]
    for j in range(break_i - 1, max(swing_i - cfg.order_block_lookback, 0) - 1, -1):
        opposite = ctx.c[j] < ctx.o[j] if bullish else ctx.c[j] > ctx.o[j]
        if opposite:
            lvl = ctx.l[j] if bullish else ctx.h[j]
            return {"name": "Bullish order block" if bullish else "Bearish order block",
                    "direction": "BULLISH" if bullish else "BEARISH", "index": j,
                    "confirmed_index": break_i, "ts": ctx.ts[j], "confirmed_ts": ctx.ts[break_i],
                    "detected_index": break_i, "detected_ts": ctx.ts[break_i],
                    "bottom": ctx.l[j], "top": ctx.h[j], "level": None,
                    "reference": {"kind": "swing_high" if bullish else "swing_low",
                                  "index": swing_i, "ts": ctx.ts[swing_i], "price": swing[1]},
                    "invalidation": {
                        "level": lvl, "rule": f"A close {'below' if bullish else 'above'} the "
                                              f"block {'low' if bullish else 'high'} {lvl:.2f}."},
                    "data_quality": dq,
                    "detail": f"Last {'bearish' if bullish else 'bullish'} candle before the "
                              f"structure break ({ctx.l[j]:.2f}–{ctx.h[j]:.2f})."}
    return None


def _base_zone(ctx: Ctx, i: int, up: bool, cfg: SMCConfig, dq: str) -> dict[str, Any] | None:
    """Demand (bullish) / supply (bearish) zone: the small-bodied base candles
    immediately before a displacement candle."""
    base: list[int] = []
    for j in range(i - 1, max(i - 1 - cfg.base_max_candles, -1), -1):
        r = ctx.h[j] - ctx.l[j]
        if r <= 0 or abs(ctx.c[j] - ctx.o[j]) > cfg.base_body_range * r:
            break
        base.append(j)
    if not base:
        return None
    first = min(base)
    bottom, top = min(ctx.l[j] for j in base), max(ctx.h[j] for j in base)
    lvl = bottom if up else top
    return {"name": "Demand zone" if up else "Supply zone",
            "direction": "BULLISH" if up else "BEARISH", "index": first,
            "confirmed_index": i, "ts": ctx.ts[first], "confirmed_ts": ctx.ts[i],
            "detected_index": i, "detected_ts": ctx.ts[i], "bottom": bottom, "top": top,
            "level": None,
            "reference": {"kind": "displacement_candle", "index": i, "ts": ctx.ts[i],
                          "price": ctx.c[i]},
            "invalidation": {"level": lvl,
                             "rule": f"A close {'below' if up else 'above'} {lvl:.2f} "
                                     f"(zone {'bottom' if up else 'top'})."},
            "data_quality": dq,
            "detail": f"{len(base)}-candle base {bottom:.2f}–{top:.2f} before a "
                      f"{'bullish' if up else 'bearish'} displacement."}


def _breakout_followups(ctx: Ctx, events: list[dict[str, Any]], cfg: SMCConfig,
                        dq: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """After each BOS/CHOCH: a retest that holds, or a failed breakout.

    Bullish break at bar i of level L. For j = i+1 …:
    - Failed breakout: close back below L within failed_breakout_window bars.
    - Breakout and retest: low within retest_tol_atr × ATR of L and close above
      L, within retest_window bars.
    The first of the two ends the search. Bearish breaks are mirrored.
    """
    n = len(ctx)
    retests, failed = [], []
    for e in events:
        i, lvl, up = e["confirmed_index"], e["level"], e["direction"] == "BULLISH"
        horizon = max(cfg.retest_window, cfg.failed_breakout_window)
        for j in range(i + 1, min(i + horizon, n - 1) + 1):
            a = ctx.s["atr14"][j]
            if a is None:
                break
            back = ctx.c[j] < lvl if up else ctx.c[j] > lvl
            if back:
                if j - i <= cfg.failed_breakout_window:
                    far = ctx.h[i] if up else ctx.l[i]
                    failed.append({
                        "name": "Failed breakout" if up else "Failed breakdown",
                        "direction": "BEARISH" if up else "BULLISH", "index": i,
                        "confirmed_index": j, "ts": ctx.ts[i], "confirmed_ts": ctx.ts[j],
                        "detected_index": j, "detected_ts": ctx.ts[j], "level": lvl,
                        "reference": {"kind": e["name"], "index": i, "ts": ctx.ts[i],
                                      "price": lvl},
                        "invalidation": {"level": far, "rule": f"A close "
                                         f"{'above' if up else 'below'} the breakout bar's "
                                         f"{'high' if up else 'low'} {far:.2f}."},
                        "data_quality": dq,
                        "detail": f"{e['name']} through {lvl:.2f} reversed within {j - i} bars."})
                break
            touched = (ctx.l[j] <= lvl + cfg.retest_tol_atr * a) if up \
                else (ctx.h[j] >= lvl - cfg.retest_tol_atr * a)
            if touched and j - i <= cfg.retest_window:
                retests.append({
                    "name": "Breakout and retest" if up else "Breakdown and retest",
                    "direction": "BULLISH" if up else "BEARISH", "index": i,
                    "confirmed_index": j, "ts": ctx.ts[i], "confirmed_ts": ctx.ts[j],
                    "detected_index": j, "detected_ts": ctx.ts[j], "level": lvl,
                    "reference": {"kind": e["name"], "index": i, "ts": ctx.ts[i], "price": lvl},
                    "invalidation": {"level": lvl, "rule": f"A close {'below' if up else 'above'}"
                                     f" the retested level {lvl:.2f}."},
                    "data_quality": dq,
                    "detail": f"Retested {lvl:.2f} {j - i} bars after the {e['name']} and held."})
                break
    key = lambda x: (x["confirmed_index"], x["name"])  # noqa: E731
    return sorted(retests, key=key), sorted(failed, key=key)


def _swings(ctx: Ctx, cfg: SMCConfig, dq: str) -> list[dict[str, Any]]:
    names = {"HH": "Higher high", "LH": "Lower high", "EH": "Equal high", "H": "Swing high",
             "HL": "Higher low", "LL": "Lower low", "EL": "Equal low", "L": "Swing low"}
    out = []
    for s in swing_points(ctx.h, ctx.l, cfg.pivot_left, cfg.pivot_right):
        i, ci = s["index"], s["confirmed_index"]
        high = s["kind"] == "HIGH"
        direction = ("BULLISH" if s["label"] in ("HH", "HL") else "BEARISH"
                     if s["label"] in ("LH", "LL") else "NEUTRAL")
        out.append({"name": names[s["label"]], "label": s["label"], "direction": direction,
                    "index": i, "confirmed_index": ci, "ts": ctx.ts[i],
                    "confirmed_ts": ctx.ts[ci], "detected_index": i, "detected_ts": ctx.ts[i],
                    "level": s["price"],
                    "reference": {"kind": "pivot", "left": cfg.pivot_left,
                                  "right": cfg.pivot_right},
                    "invalidation": {"level": s["price"], "rule": f"A close "
                                     f"{'above' if high else 'below'} {s['price']:.2f} breaks "
                                     "this swing (a BOS/CHOCH event)."},
                    "data_quality": dq,
                    "detail": f"Confirmed swing {'high' if high else 'low'} {s['price']:.2f} "
                              f"({s['label']})."})
    return out


def premium_discount(ctx: Ctx, cfg: SMCConfig | None = None, dq: str = "NOT_CHECKED",
                     as_of: int | None = None) -> dict[str, Any] | None:
    """Position of the close inside the latest confirmed swing range.

    Range = the most recent confirmed swing high and swing low known at as_of.
    PREMIUM above midpoint + band, DISCOUNT below midpoint − band, else
    EQUILIBRIUM; ABOVE_RANGE / BELOW_RANGE if the close is outside it.
    """
    cfg = cfg or DEFAULT
    t = len(ctx) - 1 if as_of is None else as_of
    ph, pl = confirmed_pivots(ctx.h[:t + 1], ctx.l[:t + 1], cfg.pivot_left, cfg.pivot_right)
    if not ph or not pl:
        return None
    (ih, hi), (il, lo) = ph[-1], pl[-1]
    if hi <= lo:
        return None
    c = ctx.c[t]
    pos = (c - lo) / (hi - lo)
    band = cfg.equilibrium_band
    zone = ("ABOVE_RANGE" if pos > 1 else "BELOW_RANGE" if pos < 0 else "PREMIUM"
            if pos > 0.5 + band else "DISCOUNT" if pos < 0.5 - band else "EQUILIBRIUM")
    ci = max(ih, il) + cfg.pivot_right
    return {"name": "Premium/discount", "zone": zone, "position": round(pos, 4),
            "range_high": hi, "range_low": lo, "equilibrium": (hi + lo) / 2,
            "high_ts": ctx.ts[ih], "low_ts": ctx.ts[il], "confirmed_ts": ctx.ts[ci],
            "detected_ts": ctx.ts[t],
            "reference": {"kind": "latest_confirmed_swing_range", "high": hi, "low": lo},
            "invalidation": {"level": None, "rule": "Recomputed when a new swing is confirmed; "
                             "a close outside the range makes it ABOVE/BELOW_RANGE."},
            "data_quality": dq,
            "detail": f"Close at {pos:.0%} of the swing range {lo:.2f}–{hi:.2f}."}


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


def _equal_levels(ctx: Ctx, ph: list[tuple[int, float]], pl: list[tuple[int, float]],
                  cfg: SMCConfig, dq: str) -> list[dict[str, Any]]:
    out = []
    for pts, name, direction in ((ph, "Equal highs", "BEARISH"), (pl, "Equal lows", "BULLISH")):
        for (i1, p1), (i2, p2) in zip(pts, pts[1:], strict=False):
            a = ctx.s["atr14"][i2]
            if a and abs(p1 - p2) <= cfg.equal_level_atr * a:
                ci = i2 + cfg.pivot_right
                high = name == "Equal highs"
                lvl = max(p1, p2) if high else min(p1, p2)
                out.append({"name": name, "direction": direction, "index": i1,
                            "confirmed_index": ci, "ts": ctx.ts[i1], "confirmed_ts": ctx.ts[ci],
                            "detected_index": i2, "detected_ts": ctx.ts[i2],
                            "level": (p1 + p2) / 2,
                            "reference": {"kind": "swing_pair", "index": [i1, i2],
                                          "price": [p1, p2]},
                            "invalidation": {"level": lvl, "rule": f"A close "
                                             f"{'above' if high else 'below'} {lvl:.2f} takes "
                                             "the resting liquidity."},
                            "data_quality": dq,
                            "detail": f"Two swings at {p1:.2f} and {p2:.2f}: resting liquidity."})
    return out
