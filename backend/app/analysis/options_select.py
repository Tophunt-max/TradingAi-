"""Options contract shortlisting for a directional view (research only).

Separates the view on the underlying from the option itself: a correct
direction can still lose money on an option through theta, IV and spread.
"""

from __future__ import annotations

from typing import Any

MAX_SPREAD_PCT = 2.0
MIN_OI = 1


def shortlist(chain: dict[str, Any], side: str) -> dict[str, Any]:
    """Pick ATM and one-step-OTM legs for CE or PE and check liquidity."""
    rows = chain.get("rows") or []
    atm_i = next((i for i, r in enumerate(rows) if r.get("is_atm")), None)
    out: dict[str, Any] = {"underlying": chain.get("underlying"), "expiry": chain.get("expiry"),
                           "side": side, "lot_size": chain.get("lot_size"), "candidates": []}
    if atm_i is None:
        out["note"] = "No at-the-money strike in the chain."
        return out
    leg_key = "call" if side == "CE" else "put"
    otm_i = atm_i + 1 if side == "CE" else atm_i - 1
    for label, i in (("ATM", atm_i), ("1 strike OTM", otm_i)):
        if not 0 <= i < len(rows):
            continue
        leg = rows[i].get(leg_key)
        if not leg:
            continue
        ltp, bid, ask = leg.get("ltp"), leg.get("bid"), leg.get("ask")
        spread = ((ask - bid) / ltp * 100) if ltp and bid and ask else None
        lot = chain.get("lot_size")
        checks = [
            {"label": "Bid and ask present", "passed": bool(bid and ask)},
            {"label": f"Spread ≤ {MAX_SPREAD_PCT}% of premium",
             "passed": spread is not None and spread <= MAX_SPREAD_PCT},
            {"label": "Open interest present", "passed": (leg.get("oi") or 0) >= MIN_OI},
            {"label": "Lot size from instrument master", "passed": bool(lot)},
        ]
        out["candidates"].append({
            "label": label, "strike": rows[i]["strike"],
            "trading_symbol": leg.get("trading_symbol"),
            "ltp": ltp, "bid": bid, "ask": ask, "spread_pct": round(spread, 2) if spread else None,
            "iv": leg.get("iv"), "delta": leg.get("delta"), "theta": leg.get("theta"),
            "oi": leg.get("oi"), "premium_per_lot": round(ask * lot, 2) if ask and lot else None,
            "theta_per_lot_per_day": round(leg["theta"] * lot, 2)
            if leg.get("theta") is not None and lot else None,
            "checks": checks, "eligible": all(c["passed"] for c in checks),
        })
    out["note"] = ("Premium excludes brokerage, taxes and slippage. Estimated charges and the "
                   "independent risk check arrive in Phase 6.")
    return out
