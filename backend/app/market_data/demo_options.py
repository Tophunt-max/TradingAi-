"""Synthetic DEMO option chain built on demo spot prices.

Expiry dates, strike spacing and the IV smile here are illustrative demo
assumptions, NOT exchange metadata. Lot size and tick size are deliberately
returned as None: they must come from the authorised instrument master
(Phase 3), never from hard-coded guesses.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta

from app.market_data.demo import INSTRUMENTS, _noise, price_at
from app.options.greeks import black_scholes

STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}
BASE_IV = {"NIFTY": 0.12, "BANKNIFTY": 0.15}
RISK_FREE = 0.065
EXPIRY_TIME_UTC = time(10, 0)  # 15:30 IST


def demo_expiries(now: datetime, count: int = 4) -> list[date]:
    first = (now + timedelta(days=1)).date()
    return [first + timedelta(days=7 * k) for k in range(count)]


def chain(underlying: str, expiry: date, now: datetime, strikes_each_side: int = 10) -> dict:
    if underlying not in STRIKE_STEP:
        raise KeyError(underlying)
    spot = price_at(underlying, now)
    step = STRIKE_STEP[underlying]
    atm = round(spot / step) * step
    expiry_dt = datetime.combine(expiry, EXPIRY_TIME_UTC, UTC)
    years = max((expiry_dt - now).total_seconds(), 60) / (365 * 24 * 3600)
    base_iv = BASE_IV[underlying] * (1 + 0.1 * _noise(f"{underlying}:iv:{now.date()}"))
    rows = []
    for k in range(-strikes_each_side, strikes_each_side + 1):
        strike = atm + k * step
        moneyness = math.log(strike / spot)
        iv = base_iv * (1 + 2.5 * moneyness * moneyness * 100 / 4 + 0.15 * abs(moneyness) * 10)
        row = {"strike": strike, "is_atm": strike == atm}
        for side, is_call in (("call", True), ("put", False)):
            g = black_scholes(spot=spot, strike=strike, years=years, vol=iv, rate=RISK_FREE,
                              call=is_call)
            ltp = max(g.price, 0.05)
            key = f"{underlying}:{expiry}:{strike}:{side}"
            spread = max(0.05, ltp * (0.004 + 0.01 * abs(moneyness) * 10))
            dist = abs(k) + 1
            oi = int((abs(_noise(key + ":oi")) * 4e6 + 2e5) / dist)
            row[side] = {
                "ltp": round(ltp, 2),
                "bid": round(max(ltp - spread / 2, 0.05), 2),
                "ask": round(ltp + spread / 2, 2),
                "iv": round(iv * 100, 2),
                "delta": round(g.delta, 4),
                "gamma": round(g.gamma, 6),
                "theta": round(g.theta, 2),
                "vega": round(g.vega, 2),
                "oi": oi,
                "oi_change": int(oi * 0.2 * _noise(key + f":doi:{now.date()}")),
                "volume": int(abs(_noise(key + f":v:{now.hour}")) * oi * 0.6),
            }
        rows.append(row)
    total_call_oi = sum(r["call"]["oi"] for r in rows)
    total_put_oi = sum(r["put"]["oi"] for r in rows)
    return {
        "underlying": underlying,
        "underlying_name": INSTRUMENTS[underlying].name,
        "expiry": expiry,
        "spot": round(spot, 2),
        "atm_strike": atm,
        "strike_step": step,
        "lot_size": None,
        "tick_size": None,
        "pcr": round(total_put_oi / total_call_oi, 2) if total_call_oi else None,
        "rows": rows,
    }
