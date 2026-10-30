"""Implied-volatility context: IV rank/percentile, skew and term structure.

Nothing here invents history. IV rank and percentile need a stored series of
past IV observations; with fewer than MIN_IV_HISTORY points they are None.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MIN_IV_HISTORY = 60  # observations (e.g. 60 daily closes ≈ 3 months)
INSUFFICIENT_IV_HISTORY = "insufficient IV history"


def iv_rank(history: Sequence[float | None] | None, current: float | None) -> dict[str, Any]:
    """IV rank = (IV − min) / (max − min); IV percentile = share of history < IV.

    Both use the provided history only. Returns None values with a reason when
    the history is shorter than MIN_IV_HISTORY or the current IV is missing.
    """
    vals = [float(v) for v in (history or []) if v is not None]
    out: dict[str, Any] = {"iv_rank": None, "iv_percentile": None, "history_points": len(vals),
                           "min_history": MIN_IV_HISTORY, "reason": None}
    if current is None:
        out["reason"] = "current IV unavailable"
        return out
    if len(vals) < MIN_IV_HISTORY:
        out["reason"] = INSUFFICIENT_IV_HISTORY
        return out
    lo, hi = min(vals), max(vals)
    out["iv_rank"] = round((current - lo) / (hi - lo), 4) if hi > lo else 0.5
    out["iv_percentile"] = round(sum(1 for v in vals if v < current) / len(vals), 4)
    return out


def _atm_index(rows: list[dict[str, Any]]) -> int | None:
    return next((i for i, r in enumerate(rows) if r.get("is_atm")), None)


def _iv(row: dict[str, Any] | None, leg: str) -> float | None:
    if not row or not row.get(leg):
        return None
    return row[leg].get("iv")


def skew(rows: list[dict[str, Any]], k: int = 2) -> dict[str, Any]:
    """Put-minus-call IV (vol points) at ±k strikes from ATM, and at 25 delta.

    ±k: put IV k strikes below ATM minus call IV k strikes above ATM (equal
    OTM distance in strikes). 25-delta: put whose delta is nearest −0.25 minus
    call whose delta is nearest +0.25, only when deltas are present.
    """
    out: dict[str, Any] = {"k_strikes": k, "put_minus_call_iv_k": None,
                           "put_minus_call_iv_25d": None, "reason": None}
    a = _atm_index(rows)
    if a is None:
        out["reason"] = "no ATM strike"
        return out
    if a - k >= 0 and a + k < len(rows):
        p, c = _iv(rows[a - k], "put"), _iv(rows[a + k], "call")
        if p is not None and c is not None:
            out["put_minus_call_iv_k"] = round(p - c, 3)
            out["strikes_k"] = {"put": rows[a - k]["strike"], "call": rows[a + k]["strike"]}
    puts = [(r["strike"], r["put"]) for r in rows if r.get("put")
            and r["put"].get("delta") is not None and r["put"].get("iv") is not None]
    calls = [(r["strike"], r["call"]) for r in rows if r.get("call")
             and r["call"].get("delta") is not None and r["call"].get("iv") is not None]
    if puts and calls:
        ks_p, p25 = min(puts, key=lambda x: abs(x[1]["delta"] + 0.25))
        ks_c, c25 = min(calls, key=lambda x: abs(x[1]["delta"] - 0.25))
        if abs(p25["delta"] + 0.25) <= 0.1 and abs(c25["delta"] - 0.25) <= 0.1:
            out["put_minus_call_iv_25d"] = round(p25["iv"] - c25["iv"], 3)
            out["strikes_25d"] = {"put": ks_p, "call": ks_c, "put_delta": p25["delta"],
                                  "call_delta": c25["delta"]}
    if out["put_minus_call_iv_k"] is None and out["put_minus_call_iv_25d"] is None:
        out["reason"] = "not enough strikes with IV"
    return out


def atm_iv(rows: list[dict[str, Any]]) -> float | None:
    a = _atm_index(rows)
    if a is None:
        return None
    vals = [v for v in (_iv(rows[a], "call"), _iv(rows[a], "put")) if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def term_structure(chains: list[dict[str, Any]]) -> dict[str, Any]:
    """ATM IV (mean of ATM call and put IV) per available expiry, nearest first."""
    pts = [{"expiry": c["expiry"], "atm_iv": atm_iv(c.get("rows") or [])} for c in chains]
    pts = [p for p in pts if p["atm_iv"] is not None]
    shape = None
    if len(pts) >= 2:
        d = pts[-1]["atm_iv"] - pts[0]["atm_iv"]
        shape = "CONTANGO" if d > 0.25 else "BACKWARDATION" if d < -0.25 else "FLAT"
    return {"points": pts, "shape": shape,
            "reason": None if len(pts) >= 2 else "fewer than two expiries with ATM IV"}
