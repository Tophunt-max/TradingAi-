"""Strategy-specific evaluation on the latest closed candle.

Each strategy returns the conditions it checked and whether they were met.
A signal is a research observation, never an order: the risk manager and the
cost model must approve any trade (Phase 6).
"""

from __future__ import annotations

from typing import Any

from app.analysis.context import Ctx
from app.analysis.patterns import pivots
from app.strategies.catalog import STRATEGIES, TEMPLATE_ORDER

RECENT = 3  # bars within which a crossover/pattern counts as "recent"


def _cond(label: str, met: bool | None) -> dict[str, Any]:
    return {"label": label, "met": met}


def _crossed(fast: list[float | None], slow: list[float | None], up: bool, within: int) -> bool:
    n = len(fast)
    for i in range(max(1, n - within), n):
        a0, b0, a1, b1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
        if None in (a0, b0, a1, b1):
            continue
        if up and a0 <= b0 and a1 > b1:
            return True
        if not up and a0 >= b0 and a1 < b1:
            return True
    return False


def _recent(events: list[dict[str, Any]], n: int, names: set[str], direction: str,
            within: int) -> list[dict[str, Any]]:
    return [e for e in events if e["name"] in names and e["direction"] == direction
            and e["confirmed_index"] >= n - within]


def _pick(bull: list[dict[str, Any]],
          bear: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Choose the side whose conditions all passed; NONE if neither or both."""
    b_ok = all(c["met"] for c in bull)
    s_ok = all(c["met"] for c in bear)
    if b_ok and not s_ok:
        return "CE", bull
    if s_ok and not b_ok:
        return "PE", bear
    return "NONE", bull if sum(bool(c["met"]) for c in bull) >= sum(
        bool(c["met"]) for c in bear) else bear


def evaluate(ctx: Ctx, timeframe: str, regime: dict[str, Any] | None,
             patterns: list[dict[str, Any]], smc: dict[str, Any],
             prediction: dict[str, Any] | None) -> list[dict[str, Any]]:
    n = len(ctx)
    s = ctx.s
    c = ctx.c[-1]
    rsi, vwap, rvol = s["rsi14"][-1], s["vwap"][-1], s["rvol20"][-1]
    ph, pl = pivots(ctx)
    res = ph[-1][1] if ph else None
    sup = pl[-1][1] if pl else None
    out = []
    for sid in TEMPLATE_ORDER:
        meta = STRATEGIES[sid]
        base = {"id": sid, "name": meta["name"], "version": meta["version"]}
        if meta["research_only"]:
            out.append({**base, "direction": "SKIPPED", "regime_compatible": None,
                        "conditions": [], "summary": "Research-only strategy; not evaluated."})
            continue
        if sid == "custom":
            out.append({**base, "direction": "SKIPPED", "regime_compatible": None,
                        "conditions": [], "summary": "Custom rules are evaluated per bot."})
            continue

        if sid == "momentum":
            bull = [_cond("EMA 9 crossed above EMA 21 (last 3 bars)",
                          _crossed(s["ema9"], s["ema21"], True, RECENT)),
                    _cond("RSI 14 above 55", rsi is not None and rsi > 55),
                    _cond("Close above VWAP", None if vwap is None else c > vwap)]
            bear = [_cond("EMA 9 crossed below EMA 21 (last 3 bars)",
                          _crossed(s["ema9"], s["ema21"], False, RECENT)),
                    _cond("RSI 14 below 45", rsi is not None and rsi < 45),
                    _cond("Close below VWAP", None if vwap is None else c < vwap)]
        elif sid == "scalping":
            bull = [_cond("Timeframe 1m or 3m", timeframe in ("1m", "3m")),
                    _cond("EMA 5 crossed above EMA 13", _crossed(s["ema5"], s["ema13"], True,
                                                                 RECENT)),
                    _cond("Close above VWAP", None if vwap is None else c > vwap)]
            bear = [_cond("Timeframe 1m or 3m", timeframe in ("1m", "3m")),
                    _cond("EMA 5 crossed below EMA 13", _crossed(s["ema5"], s["ema13"], False,
                                                                 RECENT)),
                    _cond("Close below VWAP", None if vwap is None else c < vwap)]
        elif sid == "breakout":
            bull = [_cond(f"Close above last swing high{f' {res:.2f}' if res else ''}",
                          res is not None and c > res),
                    _cond("Relative volume ≥ 1.5", None if rvol is None else rvol >= 1.5)]
            bear = [_cond(f"Close below last swing low{f' {sup:.2f}' if sup else ''}",
                          sup is not None and c < sup),
                    _cond("Relative volume ≥ 1.5", None if rvol is None else rvol >= 1.5)]
        elif sid == "reversal":
            bull_p = _recent(patterns, n, {"Hammer", "Bullish engulfing", "Morning star"},
                             "BULLISH", RECENT)
            bear_p = _recent(patterns, n, {"Shooting star", "Bearish engulfing", "Evening star"},
                             "BEARISH", RECENT)
            bull = [_cond("RSI 14 below 35", rsi is not None and rsi < 35),
                    _cond("Bullish reversal candle (last 3 bars)", bool(bull_p))]
            bear = [_cond("RSI 14 above 65", rsi is not None and rsi > 65),
                    _cond("Bearish reversal candle (last 3 bars)", bool(bear_p))]
        elif sid == "smc":
            ev = smc["structure_events"]
            open_ob = [z for z in smc["order_blocks"] if not z["mitigated"]
                       or z["mitigated_index"] >= n - RECENT]
            bull = [_cond("Bullish CHOCH in the last 10 bars",
                          bool(_recent(ev, n, {"CHOCH"}, "BULLISH", 10))),
                    _cond("Price at a bullish order block",
                          any(z["direction"] == "BULLISH" and z["bottom"] <= ctx.l[-1] <= z["top"]
                              for z in open_ob))]
            bear = [_cond("Bearish CHOCH in the last 10 bars",
                          bool(_recent(ev, n, {"CHOCH"}, "BEARISH", 10))),
                    _cond("Price at a bearish order block",
                          any(z["direction"] == "BEARISH" and z["bottom"] <= ctx.h[-1] <= z["top"]
                              for z in open_ob))]
        elif sid == "ai_adaptive":
            if not prediction or not prediction.get("probabilities"):
                out.append({**base, "direction": "UNAVAILABLE", "regime_compatible": None,
                            "conditions": [_cond("A promoted, calibrated model exists", False)],
                            "summary": "Needs a promoted model that passed out-of-sample "
                                       "calibration checks."})
                continue
            p = prediction["probabilities"]
            bull = [_cond("P(UP) ≥ 70% (research filter)", p["UP"] >= 0.7)]
            bear = [_cond("P(DOWN) ≥ 70% (research filter)", p["DOWN"] >= 0.7)]
        else:  # pragma: no cover - catalogue and rules out of sync
            continue

        direction, conds = _pick(bull, bear)
        fam = regime["family"] if regime else None
        compatible = None if fam is None else ("ANY" in meta["regimes"] or fam in meta["regimes"])
        if direction == "NONE":
            summary = "No signal: not all conditions are met."
        elif compatible is False:
            summary = (f"{direction} conditions met, but the {regime['label'] if regime else ''} "
                       f"regime is outside this strategy's design ({', '.join(meta['regimes'])}).")
        else:
            summary = f"{direction} conditions met on the last closed candle."
        out.append({**base, "direction": direction, "regime_compatible": compatible,
                    "conditions": conds, "summary": summary})
    return out
