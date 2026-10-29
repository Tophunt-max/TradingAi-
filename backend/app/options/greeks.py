"""Black-Scholes pricing and Greeks for European options (no dividends).

Used to derive consistent demo option-chain values and, later, to check broker
or vendor Greeks. Theta is per calendar day; vega is per 1 volatility point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float


def black_scholes(
    *, spot: float, strike: float, years: float, vol: float, rate: float, call: bool
) -> Greeks:
    if years <= 0 or vol <= 0:
        intrinsic = max(spot - strike, 0) if call else max(strike - spot, 0)
        itm = intrinsic > 0
        return Greeks(intrinsic, (1.0 if call else -1.0) if itm else 0.0, 0.0, 0.0, 0.0)
    sq = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * sq)
    d2 = d1 - vol * sq
    disc = math.exp(-rate * years)
    if call:
        price = spot * _cdf(d1) - strike * disc * _cdf(d2)
        delta = _cdf(d1)
        theta = -spot * _pdf(d1) * vol / (2 * sq) - rate * strike * disc * _cdf(d2)
    else:
        price = strike * disc * _cdf(-d2) - spot * _cdf(-d1)
        delta = _cdf(d1) - 1
        theta = -spot * _pdf(d1) * vol / (2 * sq) + rate * strike * disc * _cdf(-d2)
    gamma = _pdf(d1) / (spot * vol * sq)
    vega = spot * _pdf(d1) * sq / 100
    return Greeks(price, delta, gamma, theta / 365, vega)
