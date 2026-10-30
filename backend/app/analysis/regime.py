"""Rule-based market-regime detection at a given candle (causal)."""

from __future__ import annotations

import math
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


# ------------------------------------------------------------------ regime v2
#
# Deterministic feature scores per state -> softmax -> confidence scores.
# The scores are NOT calibrated probabilities (calibration_status says so).
# See docs/ai-models.md "Regime detection v2".

V2_VERSION = "regime-v2.0"
STATES = ["STRONG_UPTREND", "STRONG_DOWNTREND", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY",
          "BREAKOUT", "REVERSAL", "ABNORMAL", "EVENT_UNCERTAINTY"]
UNKNOWN = "UNKNOWN"
NO_TRADE_STATES = {"ABNORMAL", "EVENT_UNCERTAINTY", UNKNOWN}
# New state -> legacy family, so catalogue regimes ("TRENDING", "RANGE", ...) keep working.
STATE_FAMILY: dict[str, str | None] = {
    "STRONG_UPTREND": "TRENDING", "STRONG_DOWNTREND": "TRENDING", "RANGE": "RANGE",
    "LOW_VOLATILITY": "RANGE", "HIGH_VOLATILITY": "VOLATILE", "BREAKOUT": "RANGE_EXPANSION",
    "REVERSAL": "RANGE", "ABNORMAL": None, "EVENT_UNCERTAINTY": None, UNKNOWN: None}
TEMPERATURE = 0.6
MIN_CONFIDENCE = 0.30
MIN_MARGIN = 0.05
ABNORMAL_ATR_MULT = 3.0
ABNORMAL_PCTL = 0.99
STRUCTURE_WINDOW = 10
EVENT_SCORE = {"HIGH": 4.0, "MEDIUM": 1.5, "LOW": 0.0, "NONE": 0.0}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def softmax(scores: dict[str, float], temperature: float = TEMPERATURE) -> dict[str, float]:
    m = max(scores.values())
    e = {k: math.exp((v - m) / temperature) for k, v in scores.items()}
    tot = sum(e.values())
    return {k: v / tot for k, v in e.items()}


def _unknown(reason: str, legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": UNKNOWN, "family": None, "confidence": {}, "top": [], "margin": None,
            "calibration_status": "UNCALIBRATED", "no_trade": True, "reasons": [reason],
            "features": {}, "unavailable": ["breadth"], "version": V2_VERSION,
            "legacy_label": legacy["label"] if legacy else None, "method": _METHOD}


_METHOD = ("Deterministic feature scores (trend, volatility percentile, range compression, "
           "structure breaks, volume, event context) mapped through a softmax. Confidence "
           "scores are UNCALIBRATED, not probabilities.")


def state_scores(f: dict[str, Any], event_risk_level: str | None,
                 quality_status: str | None) -> dict[str, float]:
    """Map feature values to a raw score per state (0 = no evidence)."""
    adx_z = _clip((f["adx"] - 20) / 10, 0, 2)
    up_agree = (f["slope_atr"] > 0) + (f["close_vs_ema50"] > 0) + (f["pdi"] > f["mdi"])
    dn_agree = (f["slope_atr"] < 0) + (f["close_vs_ema50"] < 0) + (f["pdi"] < f["mdi"])
    slope_mag = _clip(abs(f["slope_atr"]), 0, 1.5)
    s = {k: 0.0 for k in STATES}
    s["STRONG_UPTREND"] = adx_z * up_agree / 3 * (1 + slope_mag)
    s["STRONG_DOWNTREND"] = adx_z * dn_agree / 3 * (1 + slope_mag)
    s["HIGH_VOLATILITY"] = 3 * _clip((f["atr_rank"] - 0.7) / 0.3, 0, 1)
    s["LOW_VOLATILITY"] = (1.5 * _clip((0.3 - f["atr_rank"]) / 0.3, 0, 1)
                           + 1.5 * _clip((0.25 - f["width_rank"]) / 0.25, 0, 1))
    s["RANGE"] = (1.5 * _clip((25 - f["adx"]) / 10, 0, 1)
                  + (0.5 if not f["recent_bos"] and not f["recent_choch"] else 0.0)
                  + 0.5 * _clip(1 - abs(f["slope_atr"]) * 2, 0, 1))
    s["BREAKOUT"] = (2.0 * (f["prev_width_rank"] <= 0.2 and f["width_rank"] >= 0.5)
                     + 1.0 * bool(f["recent_bos"]) + 0.5 * (f["rvol"] is not None
                                                            and f["rvol"] >= 1.5))
    s["REVERSAL"] = (1.5 * bool(f["recent_choch"])
                     + 0.5 * (f["rsi"] is not None and (f["rsi"] > 70 or f["rsi"] < 30)
                              and bool(f["recent_choch"])))
    s["ABNORMAL"] = 4.0 if f["abnormal_move"] else 0.0
    if quality_status == "FAIL":
        s["ABNORMAL"] = 6.0
    s["EVENT_UNCERTAINTY"] = EVENT_SCORE.get((event_risk_level or "NONE").upper(), 0.0)
    return s


def detect_v2(ctx: Ctx, i: int | None = None, *, smc: dict[str, Any] | None = None,
              event_risk_level: str | None = None,
              quality_status: str | None = None) -> dict[str, Any]:
    """Regime confidence over STATES at candle i (causal: uses candles[0..i] only).

    `smc` is the output of analysis.smc.analyse for the same ctx (optional);
    only structure events confirmed at or before i are used.
    """
    i = len(ctx) - 1 if i is None else i
    legacy = detect(ctx, i)
    if legacy is None:
        return _unknown("Not enough history for the regime features.")
    s = ctx.s
    start = max(1, i - LOOKBACK)
    atr_prev = [s["atr14"][k - 1] for k in range(start, i + 1)]
    moves = [max(abs(ctx.c[k] - ctx.c[k - 1]), abs(ctx.o[k] - ctx.c[k - 1])) / a
             for k, a in zip(range(start, i + 1), atr_prev, strict=True) if a]
    cur_move = moves[-1] if moves else 0.0
    pctl = sorted(moves[:-1])[int(ABNORMAL_PCTL * (len(moves) - 2))] if len(moves) > 20 else None
    abnormal = cur_move >= ABNORMAL_ATR_MULT and (pctl is None or cur_move >= pctl)
    atr_v = s["atr14"][i]
    ema50, ema50_prev = s["ema50"][i], s["ema50"][i - 5]
    events = [e for e in (smc or {}).get("structure_events", []) if e["confirmed_index"] <= i]
    recent = [e for e in events if e["confirmed_index"] >= i - STRUCTURE_WINDOW]
    f = {
        "adx": legacy["adx"], "pdi": s["pdi14"][i] or 0.0, "mdi": s["mdi14"][i] or 0.0,
        "slope_atr": (ema50 - ema50_prev) / atr_v if atr_v else 0.0,
        "close_vs_ema50": (ctx.c[i] - ema50) / atr_v if atr_v else 0.0,
        "atr_rank": legacy["atr_percentile"], "width_rank": legacy["bb_width_percentile"],
        "prev_width_rank": None, "rvol": s["rvol20"][i], "rsi": s["rsi14"][i],
        "recent_bos": [e["direction"] for e in recent if e["name"] == "BOS"],
        "recent_choch": [e["direction"] for e in recent if e["name"] == "CHOCH"],
        "move_atr": round(cur_move, 3), "move_atr_p99": None if pctl is None else round(pctl, 3),
        "abnormal_move": abnormal,
    }
    widths = [(u - b) / m for u, b, m in zip(s["bb_upper"][max(0, i - LOOKBACK):i + 1],
                                             s["bb_lower"][max(0, i - LOOKBACK):i + 1],
                                             s["bb_mid"][max(0, i - LOOKBACK):i + 1],
                                             strict=True) if u is not None and b is not None
              and m]
    f["prev_width_rank"] = (_percentile_rank(widths[:-5], widths[-6]) if len(widths) > 10
                            else f["width_rank"])
    raw = state_scores(f, event_risk_level, quality_status)
    conf = softmax(raw)
    ranked = sorted(conf.items(), key=lambda kv: -kv[1])
    (top, p1), (_, p2) = ranked[0], ranked[1]
    reasons: list[str] = []
    state = top
    if p1 < MIN_CONFIDENCE:
        state = UNKNOWN
        reasons.append(f"Highest confidence {p1:.0%} is below {MIN_CONFIDENCE:.0%}.")
    elif p1 - p2 < MIN_MARGIN:
        state = UNKNOWN
        reasons.append(f"Conflicting evidence: top two states within {MIN_MARGIN:.0%} "
                       f"({ranked[0][0]} {p1:.0%}, {ranked[1][0]} {p2:.0%}).")
    if quality_status == "FAIL":
        reasons.append("Data-quality check failed: treated as ABNORMAL.")
    if abnormal:
        reasons.append(f"Last bar moved {cur_move:.1f}× ATR (≥ {ABNORMAL_ATR_MULT}× and above "
                       "the 99th percentile of recent bars).")
    if raw["EVENT_UNCERTAINTY"] > 0:
        reasons.append(f"Event risk level {event_risk_level} supplied by the caller.")
    reasons.append(f"ADX {f['adx']:.1f}; EMA50 slope {f['slope_atr']:+.2f} ATR over 5 bars; "
                   f"ATR at {f['atr_rank']:.0%} and Bollinger width at {f['width_rank']:.0%} "
                   "percentile.")
    if recent:
        reasons.append("Recent structure: " + ", ".join(
            f"{e['direction'].lower()} {e['name']}" for e in recent[-3:]) + ".")
    if smc is None:
        reasons.append("Structure events not supplied; BOS/CHOCH evidence unavailable.")
    feats = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}
    return {
        "state": state, "family": STATE_FAMILY[state],
        "confidence": {k: round(v, 4) for k, v in conf.items()},
        "raw_scores": {k: round(v, 3) for k, v in raw.items()},
        "top": [{"state": k, "confidence": round(v, 4)} for k, v in ranked[:3]],
        "margin": round(p1 - p2, 4),
        "calibration_status": "UNCALIBRATED",
        "no_trade": state in NO_TRADE_STATES,
        "reasons": reasons, "features": feats,
        "unavailable": ["breadth"] + ([] if smc is not None else ["structure_events"])
        + ([] if event_risk_level is not None else ["event_context"]),
        "thresholds": {"min_confidence": MIN_CONFIDENCE, "min_margin": MIN_MARGIN,
                       "temperature": TEMPERATURE, "abnormal_atr_mult": ABNORMAL_ATR_MULT},
        "legacy_label": legacy["label"], "version": V2_VERSION, "method": _METHOD,
    }
