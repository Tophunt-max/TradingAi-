"""The analysis pipeline (Phase 4).

  1 data validation  2 features  3 regime  4 technicals  5 patterns/structure
  6 strategy evaluation  7 AI prediction  8 contract selection  9 costs
  10 risk validation  11 proposal or NO TRADE  12 explanation

Steps 9 and 10 are not built yet, so step 11 always returns NO_TRADE. Any
setup produced here is a research observation, not a trade proposal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.analysis import context as C
from app.analysis import patterns as PAT
from app.analysis import regime as R
from app.analysis import smc as SMC
from app.analysis import technical_summary as TS
from app.analysis.options_select import shortlist
from app.indicators import structure as ST
from app.indicators.core import (
    INDICATOR_VERSION,
    candle_anatomy,
    classic_pivots,
    fibonacci,
    fibonacci_extension,
)
from app.strategies.router import route as route_strategies
from app.strategies.rules import evaluate as eval_strategies

PROB_FILTER = 0.70
RECENT_EVENTS = 12


def _step(key: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _probs_text(p: dict[str, float], sep: str) -> str:
    """UP / SIDEWAYS / DOWN as percentages; binary models have no SIDEWAYS class."""
    return sep.join(f"{k} {p[k]:.0%}" for k in ("UP", "SIDEWAYS", "DOWN") if k in p)


def _r(v: float | None, d: int = 2) -> float | None:
    return None if v is None else round(v, d)


def _public(events: list[dict[str, Any]], n: int, keep: int = RECENT_EVENTS) -> list[dict]:
    out = []
    for e in events[-keep:]:
        pub = {k: v for k, v in e.items() if k != "index" and not k.endswith("_index")}
        conf = e.get("confirmation")
        if conf is not None:
            pub["confirmation"] = {k: v for k, v in conf.items() if k != "index"} | {
                "bars_ago": None if conf["index"] is None else n - 1 - conf["index"]}
        out.append(pub | {"bars_ago": n - 1 - e["confirmed_index"]})
    return out


def indicator_snapshot(ctx: C.Ctx) -> dict[str, Any]:
    s = ctx.s
    out: dict[str, Any] = {
        "close": _r(ctx.c[-1]),
        **{f"ema{p}": _r(s[f"ema{p}"][-1]) for p in (9, 21, 50, 200)},
        "rsi14": _r(s["rsi14"][-1]), "macd": _r(s["macd"][-1]),
        "macd_signal": _r(s["macd_signal"][-1]), "macd_hist": _r(s["macd_hist"][-1]),
        "vwap": _r(s["vwap"][-1]), "supertrend": _r(s["supertrend"][-1]),
        "supertrend_direction": {1: "UP", -1: "DOWN", None: None}[s["supertrend_dir"][-1]],
        "bb_upper": _r(s["bb_upper"][-1]), "bb_lower": _r(s["bb_lower"][-1]),
        "atr14": _r(s["atr14"][-1]), "adx14": _r(s["adx14"][-1]), "pdi14": _r(s["pdi14"][-1]),
        "mdi14": _r(s["mdi14"][-1]), "cci20": _r(s["cci20"][-1]),
        "stoch_k": _r(s["stoch_k"][-1]), "stoch_d": _r(s["stoch_d"][-1]),
        "relative_volume": _r(s["rvol20"][-1]),
        "indicator_version": INDICATOR_VERSION,
        "ema100": _r(s["ema100"][-1]), "sma20": _r(s["sma20"][-1]),
        "roc12": _r(s["roc12"][-1]), "momentum10": _r(s["mom10"][-1]),
        "obv": _r(s["obv"][-1], 0), "volume_ma20": _r(s["volma20"][-1], 0),
        "hv20": _r(s["hv20"][-1], 4), "realized_vol20": _r(s["rv20"][-1], 4),
        "parkinson_vol20": _r(s["pkv20"][-1], 4),
        "volatility_state": s["vol_state"][-1], "volatility_score": _r(s["vol_score"][-1]),
        "ichimoku": {"tenkan": _r(s["ichi_tenkan"][-1]), "kijun": _r(s["ichi_kijun"][-1]),
                     "cloud_a": _r(s["ichi_span_a"][-1]), "cloud_b": _r(s["ichi_span_b"][-1]),
                     "projected_senkou_a": _r(s["ichi_senkou_a"][-1]),
                     "projected_senkou_b": _r(s["ichi_senkou_b"][-1]),
                     "note": "cloud_a/b are the spans drawn at the last candle (computed 26 "
                             "bars earlier); projected_* are drawn 26 bars ahead. Chikou is "
                             "omitted because it references future placement."},
        "candle": {k: (_r(v) if isinstance(v, float) else v) for k, v in
                   candle_anatomy(ctx.o[-1], ctx.h[-1], ctx.l[-1], ctx.c[-1]).items()},
    }
    days = C.daily_hlc(ctx)
    if ctx.intraday and len(days) >= 2:
        _, h, lo, c = days[-2]  # previous COMPLETED session only
        out["pivots"] = {k: _r(v) for k, v in classic_pivots(h, lo, c).items()}
        out["pivots_basis"] = f"Previous session {days[-2][0].isoformat()}"
    ph, pl = PAT.pivots(ctx)
    if ph and pl:
        (ih, hh), (il, ll) = ph[-1], pl[-1]
        up = il < ih  # last move went from the low to the high
        out["fibonacci"] = {k: _r(v) for k, v in fibonacci(hh, ll, up).items()}
        out["fibonacci_basis"] = (f"Retracement of the up move {ll:.2f} → {hh:.2f}" if up
                                  else f"Retracement of the down move {hh:.2f} → {ll:.2f}")
        out["fibonacci_extension"] = {k: _r(v) for k, v in
                                      fibonacci_extension(hh, ll, up).items()}
    return out


def technical_view(ctx: C.Ctx, n: int) -> dict[str, Any]:
    """Evidence families, correlation flags, divergences, trendlines and range."""
    s = ctx.s
    out = TS.evidence(ctx)
    divs = (ST.divergences(ctx.h, ctx.l, s["rsi14"], "RSI")
            + ST.divergences(ctx.h, ctx.l, s["macd_hist"], "MACD")
            + ST.divergences(ctx.h, ctx.l, s["obv"], "OBV"))
    divs.sort(key=lambda e: (e["confirmed_index"], e["name"]))
    out["divergences"] = [
        {k: v for k, v in d.items() if not k.endswith("index")}
        | {"detected_ts": ctx.ts[d["detection_index"]],
           "confirmed_ts": ctx.ts[d["confirmed_index"]],
           "bars_ago": n - 1 - d["confirmed_index"]} for d in divs[-6:]]
    tl = ST.trendlines(ctx.h, ctx.l, ctx.c, s["atr14"])
    out["trendlines"] = {k: tl[k] for k in ("resistance", "support", "channel")}
    out["range"] = ST.range_boundaries(ctx.h, ctx.l, ctx.c, s["atr14"])
    return out


def _legacy_signals(ctx: C.Ctx) -> tuple[str, list[dict[str, str]]]:
    s, c = ctx.s, ctx.c[-1]
    sig, score = [], 0
    e9, e21, e50, r = s["ema9"][-1], s["ema21"][-1], s["ema50"][-1], s["rsi14"][-1]
    if e9 is not None and e21 is not None:
        up = e9 > e21
        score += 1 if up else -1
        sig.append({"name": "EMA 9 / 21", "reading": f"EMA 9 {'above' if up else 'below'} EMA 21",
                    "bias": "BULLISH" if up else "BEARISH"})
    if e50 is not None:
        up = c > e50
        score += 1 if up else -1
        sig.append({"name": "Price vs EMA 50", "reading": f"Price {'above' if up else 'below'} "
                    "EMA 50", "bias": "BULLISH" if up else "BEARISH"})
    if r is not None:
        b = "BULLISH" if r > 55 else "BEARISH" if r < 45 else "NEUTRAL"
        score += {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0}[b]
        sig.append({"name": "RSI 14", "reading": f"{r:.1f}", "bias": b})
    st = s["supertrend_dir"][-1]
    if st is not None:
        sig.append({"name": "Supertrend (10, 3)", "reading": "Up" if st == 1 else "Down",
                    "bias": "BULLISH" if st == 1 else "BEARISH"})
    if s["macd_hist"][-1] is not None:
        sig.append({"name": "MACD histogram", "reading": f"{s['macd_hist'][-1]:.2f}",
                    "bias": "BULLISH" if s["macd_hist"][-1] > 0 else "BEARISH"})
    trend = "BULLISH" if score >= 2 else "BEARISH" if score <= -2 else "SIDEWAYS"
    return trend, sig


def _setup(ctx: C.Ctx, side: str) -> dict[str, Any] | None:
    """Underlying levels for a directional idea: entry zone, stop, targets."""
    a = ctx.s["atr14"][-1]
    if a is None:
        return None
    c = ctx.c[-1]
    ph, pl = PAT.pivots(ctx)
    if side == "CE":
        swing = next((p for _, p in reversed(pl) if p < c), None)
        stop = min(swing - 0.1 * a, c - 0.5 * a) if swing is not None else c - 1.5 * a
        risk = c - stop
        targets = [c + risk, c + 2 * risk]
        nxt = next((p for _, p in reversed(ph) if p > c), None)
    else:
        swing = next((p for _, p in reversed(ph) if p > c), None)
        stop = max(swing + 0.1 * a, c + 0.5 * a) if swing is not None else c + 1.5 * a
        risk = stop - c
        targets = [c - risk, c - 2 * risk]
        nxt = next((p for _, p in reversed(pl) if p < c), None)
    return {
        "side": side, "basis": "Underlying price levels. Option premiums do not move 1:1.",
        "entry_zone": [_r(c - 0.1 * a), _r(c + 0.1 * a)], "stop_loss": _r(stop),
        "stop_basis": "Beyond the last confirmed swing" if swing is not None else "1.5 × ATR",
        "targets": [_r(t) for t in targets], "risk_reward": [1.0, 2.0],
        "next_level": _r(nxt),
        "invalidation": [
            f"A close {'below' if side == 'CE' else 'above'} {stop:.2f}.",
            "A regime change away from the strategy's design.",
            "A data-quality failure or stale prices.",
        ],
        "target_before_stop_probability": None,
        "target_before_stop_note": "Not modelled yet; needs a calibrated path model.",
    }


def run(*, candles: list[dict[str, Any]], timeframe: str, symbol: str, quality: dict[str, Any],
        is_demo: bool, predict_fn: Callable[[C.Ctx, str | None], dict[str, Any] | None],
        chain_fn: Callable[[], dict[str, Any] | None] | None,
        event_risk_level: str | None = None,
        event_risk: dict[str, Any] | None = None) -> dict[str, Any]:
    steps: list[dict[str, str]] = []
    reasons: list[str] = []
    q_status = quality.get("status", "OK")
    steps.append(_step("data", "Market-data validation", "FAILED" if q_status == "FAIL" else
                       "DONE", f"Quality {q_status}; {len(candles)} closed candles."))
    if q_status == "FAIL":
        reasons.append("Market-data quality check failed for this window.")
    if is_demo:
        reasons.append("Input data is synthetic DEMO data.")

    ctx = C.build(candles, intraday=timeframe != "1d", timeframe=timeframe)
    n = len(ctx)
    steps.append(_step("features", "Feature engineering", "DONE",
                       "Indicators computed causally on closed candles."))
    regime = R.detect(ctx)
    steps.append(_step("regime", "Market-regime detection", "DONE" if regime else "SKIPPED",
                       regime["label"] if regime else "Not enough history."))
    trend, signals = _legacy_signals(ctx)
    snap = indicator_snapshot(ctx)
    technical = technical_view(ctx, n)
    steps.append(_step("technical", "Technical analysis", "DONE",
                       f"Trend reading {trend}; evidence families net "
                       f"{technical['net_family_vote']:+d} of {technical['max_family_votes']} "
                       f"({INDICATOR_VERSION})."))
    pats = PAT.all_patterns(ctx)
    smc = SMC.analyse(ctx, quality=q_status)
    struct = PAT.structure(ctx)
    steps.append(_step("patterns", "Patterns & market structure", "DONE",
                       f"{len(pats)} pattern events, {len(smc['structure_events'])} structure "
                       "breaks in the window."))

    regime_v2 = R.detect_v2(ctx, smc=smc, event_risk_level=event_risk_level,
                            quality_status=q_status)
    prediction = predict_fn(ctx, regime["label"] if regime else None)
    router = route_strategies(regime_v2, model_available=bool(
        prediction and prediction.get("probabilities")))
    if regime_v2["no_trade"]:
        reasons.append(f"Regime v2 state {regime_v2['state']}: the strategy router returns "
                       "NO TRADE.")
    strategies = eval_strategies(ctx, timeframe, regime, pats, smc, prediction)
    live = [s for s in strategies if s["direction"] in ("CE", "PE")]
    usable = [s for s in live if s["regime_compatible"] is not False]
    steps.append(_step("strategies", "Strategy evaluation", "DONE",
                       f"{len(live)} strategies signalled; {len(usable)} regime-compatible."))

    if prediction is None:
        steps.append(_step("prediction", "AI prediction", "SKIPPED",
                           "No promoted model for this instrument and timeframe."))
        reasons.append("No promoted, calibrated model for this instrument and timeframe.")
    elif not prediction.get("probabilities"):
        steps.append(_step("prediction", "AI prediction", "SKIPPED", prediction["reason"]))
        reasons.append(f"No probability: {prediction['reason']}")
    else:
        p = prediction["probabilities"]
        steps.append(_step("prediction", "AI prediction", "DONE",
                           _probs_text(p, " · ")))
        if max(p["UP"], p["DOWN"]) < PROB_FILTER:
            reasons.append(f"No direction reaches the {PROB_FILTER:.0%} research filter.")

    sides = {s["direction"] for s in usable}
    side = sides.pop() if len(sides) == 1 else None
    if len(sides) > 0:  # conflicting directions
        reasons.append("Strategies disagree on direction.")
        side = None
    if not usable:
        reasons.append("No regime-compatible strategy signal on the last closed candle.")

    options = None
    if side and chain_fn is not None:
        chain = chain_fn()
        options = shortlist(chain, side) if chain else None
        steps.append(_step("contract", "Options contract selection",
                           "DONE" if options else "SKIPPED",
                           f"{len(options['candidates'])} candidates checked." if options
                           else "Option chain unavailable."))
    else:
        steps.append(_step("contract", "Options contract selection", "SKIPPED",
                           "No single directional view to select a contract for." if not side
                           else "No option chain for this instrument."))
    steps.append(_step("costs", "Cost estimation", "BLOCKED",
                       "Versioned charge schedules arrive with paper trading (Phase 6)."))
    steps.append(_step("risk", "Independent risk validation", "BLOCKED",
                       "The global risk manager arrives in Phase 6."))
    reasons.append("Charges estimation and independent risk validation are not built yet "
                   "(Phase 6), so no trade can be proposed.")
    # Deterministic event-risk gate (app.events.risk); additive, never relaxes a NO TRADE.
    if event_risk and event_risk["action"] == "NO_TRADE":
        reasons.append("Event-risk gate: HIGH-importance event in the window (NO TRADE).")
    elif event_risk and event_risk["action"] == "REDUCE_EXPOSURE":
        reasons.append("Event-risk gate: MEDIUM-importance event in the window (reduce "
                       "exposure).")
    steps.append(_step("decision", "Trade proposal or NO TRADE", "DONE", "NO TRADE"))

    ph, pl = PAT.pivots(ctx)
    last = ctx.c[-1]
    support = sorted({round(p, 2) for _, p in pl if p < last}, reverse=True)[:2]
    resistance = sorted({round(p, 2) for _, p in ph if p > last})[:2]

    explanation = _explain(symbol, timeframe, regime, trend, struct, smc, pats, usable,
                           prediction, n, technical)
    steps.append(_step("explanation", "Auditable explanation", "DONE",
                       f"{len(explanation)} statements."))

    return {
        "last_price": round(last, 2), "trend": trend,
        "trend_method": "Rule-based: EMA 9/21, price vs EMA 50, RSI 14 (not a probability)",
        "signals": signals, "support": support, "resistance": resistance,
        "atr": snap["atr14"], "rsi": snap["rsi14"],
        "regime": regime, "regime_v2": regime_v2, "strategy_router": router,
        "indicators": snap, "structure": struct,
        "patterns": _public(pats, n),
        "smc": {
            "trend": smc["trend"],
            "structure_events": _public(smc["structure_events"], n, 6),
            "order_blocks": _public([z for z in smc["order_blocks"] if not z["mitigated"]], n, 4),
            "fair_value_gaps": _public([z for z in smc["fair_value_gaps"]
                                        if not z["mitigated"]], n, 4),
            "liquidity_sweeps": _public(smc["liquidity_sweeps"], n, 4),
            "equal_levels": _public(smc["equal_levels"], n, 4),
            "displacement": _public(smc["displacement"], n, 3),
            "supply_demand": _public([z for z in smc["supply_demand"] if not z["mitigated"]],
                                     n, 4),
            "breakout_retests": _public(smc["breakout_retests"], n, 3),
            "failed_breakouts": _public(smc["failed_breakouts"], n, 3),
            "swings": _public(smc["swings"], n, 6),
            "premium_discount": smc["premium_discount"],
            "data_quality": smc["data_quality"],
            "thresholds": smc["thresholds"],
            "disclaimer": smc["disclaimer"],
        },
        "technical": technical,
        "indicator_version": INDICATOR_VERSION,
        "strategies": strategies,
        "prediction": prediction,
        "probabilities": prediction.get("probabilities") if prediction else None,
        "model_version": prediction.get("model_version") if prediction else None,
        "setup_candidate": _setup(ctx, side) if side else None,
        "options": options,
        "pipeline": steps,
        "decision": "NO_TRADE",
        "event_risk": event_risk,
        "abstain_reasons": list(dict.fromkeys(reasons + [
            "Indicators and strategies share the same price data; they are correlated, not "
            "independent confirmations."])),
        "explanation": explanation,
    }


def _explain(symbol: str, timeframe: str, regime: dict | None, trend: str, struct: dict | None,
             smc: dict, pats: list[dict], usable: list[dict], prediction: dict | None,
             n: int, technical: dict | None = None) -> list[str]:
    out = []
    if regime:
        out.append(f"{symbol} on {timeframe} is in a {regime['label'].replace('_', ' ').lower()} "
                   f"regime ({'; '.join(regime['reasons'][:2])}).")
    out.append(f"The rule-based trend reading is {trend.lower()}.")
    if struct:
        out.append(f"Confirmed swings show {struct['label'].lower()}.")
    ev = smc["structure_events"]
    if ev:
        e = ev[-1]
        out.append(f"Latest structure break: {e['direction'].lower()} {e['name']} "
                   f"{n - 1 - e['confirmed_index']} bars ago.")
    recent = [p for p in pats if p["confirmed_index"] >= n - 3]
    if recent:
        out.append("Recent patterns: " + ", ".join(sorted({p["name"] for p in recent})) + ".")
        pending = [p for p in recent if p.get("confirmation", {}).get("status") == "PENDING"]
        if pending:
            out.append(f"{len(pending)} of them still need a confirming close.")
    if technical:
        fam = ", ".join(f"{f['family']} {f['bias'].lower()}" for f in technical["families"]
                        if f["directional"])
        out.append(f"Evidence families (one vote each): {fam}; "
                   f"{len(technical['correlated_pairs'])} indicator pairs have |ρ| > "
                   f"{technical['correlation_threshold']} and are not independent.")
    if usable:
        out.append("Signalling strategies: " + ", ".join(
            f"{s['name']} ({s['direction']})" for s in usable) + ".")
    else:
        out.append("No strategy has all its conditions met in a compatible regime.")
    if prediction and prediction.get("probabilities"):
        p = prediction["probabilities"]
        out.append(f"Model {prediction['model_name']}: {_probs_text(p, ', ')} "
                   f"(uncertainty {prediction['uncertainty']:.2f}).")
    out.append("Decision: NO TRADE.")
    return out
