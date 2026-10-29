"""Transparent technical summary (no machine-learning model).

This produces a rule-based description of trend, momentum and confirmed
support/resistance. It does NOT produce calibrated probabilities. Until a
calibrated model is deployed (Phase 4), `probabilities` is None and the
decision is always NO_TRADE.
"""

from __future__ import annotations

from typing import Any

from app.indicators.core import INDICATOR_VERSION, atr, confirmed_pivots, ema, macd, rsi
from app.indicators.evidence import (
    CORRELATION_THRESHOLD,
    correlated_pairs,
    correlation_matrix,
    family_votes,
)


def summarize(candles: list[dict[str, Any]], *, is_demo: bool) -> dict[str, Any]:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    last = closes[-1]
    e9, e21, e50 = ema(closes, 9)[-1], ema(closes, 21)[-1], ema(closes, 50)[-1]
    r = rsi(closes, 14)[-1]
    _, _, hist = macd(closes)
    a = atr(highs, lows, closes, 14)[-1]

    signals: list[dict[str, str]] = []
    score = 0
    if e9 is not None and e21 is not None:
        up = e9 > e21
        score += 1 if up else -1
        signals.append({"name": "EMA 9 / 21", "reading": "EMA 9 above EMA 21" if up
                        else "EMA 9 below EMA 21", "bias": "BULLISH" if up else "BEARISH"})
    if e50 is not None:
        up = last > e50
        score += 1 if up else -1
        signals.append({"name": "Price vs EMA 50", "reading": f"Price {'above' if up else 'below'}"
                        " EMA 50", "bias": "BULLISH" if up else "BEARISH"})
    if r is not None:
        bias = "BULLISH" if r > 55 else "BEARISH" if r < 45 else "NEUTRAL"
        score += {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0}[bias]
        signals.append({"name": "RSI 14", "reading": f"{r:.1f}", "bias": bias})
    if hist[-1] is not None:
        up = hist[-1] > 0
        signals.append({"name": "MACD histogram", "reading": f"{hist[-1]:.2f}",
                        "bias": "BULLISH" if up else "BEARISH"})

    trend = "BULLISH" if score >= 2 else "BEARISH" if score <= -2 else "SIDEWAYS"

    ph, pl = confirmed_pivots(highs, lows)
    resistance = sorted({round(p, 2) for _, p in ph if p > last})[:2]
    support = sorted({round(p, 2) for _, p in pl if p < last}, reverse=True)[:2]

    reasons = [
        "No calibrated prediction model is deployed yet (Phase 4), so no trade is proposed.",
        "These indicators are correlated; they are not independent confirmations.",
    ]
    if is_demo:
        reasons.insert(0, "Input data is synthetic DEMO data.")

    return {
        "last_price": round(last, 2),
        "trend": trend,
        "trend_method": "Rule-based: EMA 9/21, price vs EMA 50, RSI 14 (not a probability)",
        "signals": signals,
        "support": support,
        "resistance": resistance,
        "atr": round(a, 2) if a is not None else None,
        "rsi": round(r, 2) if r is not None else None,
        "probabilities": None,
        "model_version": None,
        "decision": "NO_TRADE",
        "abstain_reasons": reasons,
    }


# ------------------------------------------------------------- ta-3 evidence


def _sign(x: float | None, dead: float = 0.0) -> int | None:
    if x is None:
        return None
    return 1 if x > dead else -1 if x < -dead else 0


def indicator_votes(ctx: Any, i: int | None = None) -> tuple[dict[str, int | None],
                                                            dict[str, str]]:
    """Per-indicator vote (+1 bullish, −1 bearish, 0 neutral, None unavailable)
    at bar i, plus a short reading for each. Uses values known at i only.
    Keys match `app.indicators.evidence.FAMILIES`."""
    s, n = ctx.s, len(ctx)
    i = n - 1 if i is None else i
    c = ctx.c[i]
    votes: dict[str, int | None] = {}
    read: dict[str, str] = {}

    e9, e21, e50 = s["ema9"][i], s["ema21"][i], s["ema50"][i]
    if None not in (e9, e21, e50):
        votes["ema_stack"] = 1 if e9 > e21 > e50 else -1 if e9 < e21 < e50 else 0
        read["ema_stack"] = f"EMA 9 {e9:.2f} / 21 {e21:.2f} / 50 {e50:.2f}"
    st = s["supertrend_dir"][i]
    votes["supertrend"] = st
    read["supertrend"] = {1: "Up", -1: "Down", None: "n/a"}[st]
    a_, p_, m_ = s["adx14"][i], s["pdi14"][i], s["mdi14"][i]
    if None not in (a_, p_, m_):
        votes["adx_di"] = (1 if p_ > m_ else -1) if a_ >= 20 else 0
        read["adx_di"] = f"ADX {a_:.1f}, +DI {p_:.1f}, −DI {m_:.1f}"
    tk, kj, sa, sb = (s["ichi_tenkan"][i], s["ichi_kijun"][i], s["ichi_span_a"][i],
                      s["ichi_span_b"][i])
    if None not in (tk, kj, sa, sb):
        top, bot = max(sa, sb), min(sa, sb)
        votes["ichimoku"] = (1 if c > top and tk > kj else -1 if c < bot and tk < kj else 0)
        read["ichimoku"] = ("Above cloud" if c > top else "Below cloud" if c < bot
                            else "Inside cloud") + f", tenkan {'>' if tk > kj else '≤'} kijun"

    r = s["rsi14"][i]
    if r is not None:
        votes["rsi"] = 1 if r > 55 else -1 if r < 45 else 0
        read["rsi"] = f"{r:.1f}"
    mh = s["macd_hist"][i]
    votes["macd"] = _sign(mh)
    read["macd"] = "n/a" if mh is None else f"hist {mh:.2f}"
    k, d = s["stoch_k"][i], s["stoch_d"][i]
    if k is not None and d is not None:
        votes["stochastic"] = 1 if k > d and k > 50 else -1 if k < d and k < 50 else 0
        read["stochastic"] = f"%K {k:.1f} / %D {d:.1f}"
    cc = s["cci20"][i]
    if cc is not None:
        votes["cci"] = 1 if cc > 100 else -1 if cc < -100 else 0
        read["cci"] = f"{cc:.0f}"
    rc = s["roc12"][i]
    votes["roc"] = _sign(rc)
    read["roc"] = "n/a" if rc is None else f"{rc:.2f}%"

    ob, ob_prev = s["obv"][i], s["obv"][i - 10] if i >= 10 else None
    votes["obv"] = None if ob is None or ob_prev is None else _sign(ob - ob_prev)
    read["obv"] = "n/a" if votes["obv"] is None else f"10-bar change {ob - ob_prev:+.0f}"
    rv = s["rvol20"][i]
    if rv is not None:
        votes["rvol"] = (1 if c > ctx.o[i] else -1 if c < ctx.o[i] else 0) if rv >= 1.5 else 0
        read["rvol"] = f"{rv:.2f}× (directional only when ≥ 1.5×)"
    vw = s["vwap"][i]
    if vw is not None:
        votes["vwap"] = 1 if c > vw else -1 if c < vw else 0
        read["vwap"] = f"{vw:.2f}"

    state = s["vol_state"][i]
    for key in ("atr", "bollinger", "hv"):
        votes[key] = 0 if state is not None else None
    read["atr"] = "n/a" if s["atr14"][i] is None else f"{s['atr14'][i]:.2f}"
    read["bollinger"] = state or "n/a"
    read["hv"] = "n/a" if s["hv20"][i] is None else f"{s['hv20'][i] * 100:.1f}% annualised"
    return votes, read


CORRELATION_SERIES = {
    "ema_stack": lambda ctx, i: _rel(ctx.s["ema9"][i], ctx.s["ema21"][i]),
    "supertrend": lambda ctx, i: ctx.s["supertrend_dir"][i],
    "adx_di": lambda ctx, i: (None if ctx.s["pdi14"][i] is None
                              else ctx.s["pdi14"][i] - ctx.s["mdi14"][i]),
    "ichimoku": lambda ctx, i: _rel(ctx.c[i], ctx.s["ichi_kijun"][i]),
    "rsi": lambda ctx, i: ctx.s["rsi14"][i],
    "macd": lambda ctx, i: ctx.s["macd_hist"][i],
    "stochastic": lambda ctx, i: ctx.s["stoch_k"][i],
    "cci": lambda ctx, i: ctx.s["cci20"][i],
    "roc": lambda ctx, i: ctx.s["roc12"][i],
    "obv": lambda ctx, i: ctx.s["obv"][i],
    "rvol": lambda ctx, i: ctx.s["rvol20"][i],
    "vwap": lambda ctx, i: _rel(ctx.c[i], ctx.s["vwap"][i]),
    "atr": lambda ctx, i: _rel(ctx.s["atr14"][i], 0.0, ctx.c[i]),
    "bollinger": lambda ctx, i: (None if ctx.s["bb_upper"][i] is None
                                 else (ctx.s["bb_upper"][i] - ctx.s["bb_lower"][i]) / ctx.c[i]),
    "hv": lambda ctx, i: ctx.s["hv20"][i],
}


def _rel(a: float | None, b: float | None, base: float | None = None) -> float | None:
    if a is None or b is None:
        return None
    d = base if base is not None else b
    return (a - b) / d if d else None


def evidence(ctx: Any, window: int = 100, threshold: float | None = None) -> dict[str, Any]:
    """Family votes and highly-correlated indicator pairs at the last bar."""
    thr = CORRELATION_THRESHOLD if threshold is None else threshold
    n = len(ctx)
    votes, readings = indicator_votes(ctx)
    fam = family_votes(votes)
    for f in fam["families"]:
        for m in f["members"]:
            m["reading"] = readings.get(m["indicator"], "n/a")
    start = max(0, n - window)
    series = {k: [fn(ctx, i) for i in range(start, n)] for k, fn in CORRELATION_SERIES.items()}
    corr = correlation_matrix(series, window)
    pairs = correlated_pairs(corr, thr)
    net = fam["net_vote"]
    return {
        "indicator_version": INDICATOR_VERSION,
        "families": fam["families"],
        "net_family_vote": net,
        "max_family_votes": fam["max_votes"],
        "family_bias": "BULLISH" if net >= 2 else "BEARISH" if net <= -2 else "MIXED",
        "family_method": fam["method"],
        "volatility_state": ctx.s["vol_state"][-1],
        "correlated_pairs": pairs,
        "cross_family_correlated": sum(1 for p in pairs if not p["same_family"]),
        "correlation_window": len(series["rsi"]),
        "correlation_threshold": thr,
        "note": "Families vote once each so correlated indicators are not counted as "
                "independent confirmations. Families can still be correlated with each other "
                "(see cross_family_correlated). Votes are descriptive, not probabilities.",
    }
