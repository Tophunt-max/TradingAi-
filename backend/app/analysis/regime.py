"""Rule-based market-regime detection at a given candle (causal)."""

from __future__ import annotations

from typing import Any

from app.analysis.context import Ctx

LOOKBACK = 100

# Regime label -> family used by strategy compatibility in the catalogue.
FAMILY = {"TRENDING_UP": "TRENDING", "TRENDING_DOWN": "TRENDING", "VOLATILE": "VOLATILE",
          "RANGE_EXPANSION": "RANGE_EXPANSION", "SQUEEZE": "RANGE", "RANGE": "RANGE"}


def _percentile_rank(window: list[float], value: float) -> float:
    if not window:
        return 0.5
    return sum(1 for x in window if x <= value) / len(window)


def detect(ctx: Ctx, i: int | None = None) -> dict[str, Any] | None:
    i = len(ctx) - 1 if i is None else i
    s = ctx.s
    adx_v, atr_v, ema50 = s["adx14"][i], s["atr14"][i], s["ema50"][i]
    up, lo = s["bb_upper"][i], s["bb_lower"][i]
    if None in (adx_v, atr_v, ema50, up, lo) or i < 5:
        return None
    start = max(0, i - LOOKBACK)
    atr_pct = [a / c for a, c in zip(s["atr14"][start:i + 1], ctx.c[start:i + 1], strict=True)
               if a is not None]
    widths = [(u - b) / m for u, b, m in zip(s["bb_upper"][start:i + 1],
                                             s["bb_lower"][start:i + 1],
                                             s["bb_mid"][start:i + 1], strict=True)
              if u is not None and b is not None and m]
    cur_atr_pct = atr_v / ctx.c[i]
    cur_w = (up - lo) / s["bb_mid"][i]
    atr_rank = _percentile_rank(atr_pct, cur_atr_pct)
    w_rank = _percentile_rank(widths, cur_w)
    prev_w_rank = _percentile_rank(widths[:-5], widths[-6]) if len(widths) > 10 else w_rank
    ema50_prev = s["ema50"][i - 5]
    slope = (ema50 - ema50_prev) / ema50_prev if ema50_prev else 0.0

    reasons = [f"ADX {adx_v:.1f}", f"ATR {cur_atr_pct * 100:.2f}% of price "
               f"({atr_rank * 100:.0f}th percentile of last {len(atr_pct)} bars)",
               f"Bollinger width at {w_rank * 100:.0f}th percentile"]
    if atr_rank >= 0.9:
        label = "VOLATILE"
        reasons.append("Volatility is unusually high.")
    elif adx_v >= 25 and slope > 0 and ctx.c[i] > ema50:
        label = "TRENDING_UP"
        reasons.append("Strong trend strength with price above a rising EMA 50.")
    elif adx_v >= 25 and slope < 0 and ctx.c[i] < ema50:
        label = "TRENDING_DOWN"
        reasons.append("Strong trend strength with price below a falling EMA 50.")
    elif prev_w_rank <= 0.2 and w_rank >= 0.5:
        label = "RANGE_EXPANSION"
        reasons.append("Bands widened quickly after a squeeze.")
    elif w_rank <= 0.2:
        label = "SQUEEZE"
        reasons.append("Bands are compressed: low volatility, breakout risk.")
    else:
        label = "RANGE"
        reasons.append("No strong trend: range-bound conditions.")
    return {"label": label, "family": FAMILY[label], "adx": round(adx_v, 2),
            "atr_pct": round(cur_atr_pct * 100, 3), "atr_percentile": round(atr_rank, 2),
            "bb_width_percentile": round(w_rank, 2), "ema50_slope_pct": round(slope * 100, 3),
            "reasons": reasons, "method": "Rule-based (ADX, ATR and Bollinger-width percentiles)"}
