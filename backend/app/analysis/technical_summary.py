"""Transparent technical summary (no machine-learning model).

This produces a rule-based description of trend, momentum and confirmed
support/resistance. It does NOT produce calibrated probabilities. Until a
calibrated model is deployed (Phase 4), `probabilities` is None and the
decision is always NO_TRADE.
"""

from __future__ import annotations

from typing import Any

from app.indicators.core import atr, confirmed_pivots, ema, macd, rsi


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
