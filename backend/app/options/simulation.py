"""Monte Carlo model of an option premium hitting a target before a stop.

MODEL_ESTIMATE, not a forecast. Method (see docs/options-intelligence.md):

1. The underlying follows a driftless geometric Brownian motion
   S(t+dt) = S(t) · exp(−σ²dt/2 + σ√dt·Z), Z ~ N(0, 1) from a seeded generator.
   "Driftless" means no directional edge is assumed; the direction view is a
   separate output. σ is the underlying's realized volatility (annualised over
   252 sessions × 375 minutes) when available, else the contract's implied
   volatility read on the same trading-time basis.
2. At each step the premium is repriced with Black-Scholes at the remaining
   time to expiry (calendar ACT/365 day count, so time decay/theta is applied),
   a constant implied volatility (optionally shocked by a fixed number of vol
   points) and constant r and q.
3. The first step at which the modelled mid premium is ≥ target or ≤ stop
   decides the outcome. Discrete monitoring means a stop can be passed
   through (gap); the path's actual premium at that step is used as the exit.
   Paths that touch neither by the horizon exit at the horizon premium.

Outputs are per unit of premium; callers scale by lot size and add costs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.special import ndtr

TRADING_MINUTES_PER_YEAR = 252 * 375
CALENDAR_MINUTES_PER_YEAR = 365 * 24 * 60
METHOD = "mc-gbm-bs-v1"


def bs_price(spot: np.ndarray, strike: float, years: float, vol: float, rate: float,
             q: float, call: bool) -> np.ndarray:
    """Vectorised Black-Scholes-Merton price (continuous dividend yield q)."""
    spot = np.asarray(spot, dtype=float)
    if years <= 0 or vol <= 0:
        intrinsic = spot - strike if call else strike - spot
        return np.maximum(intrinsic, 0.0)
    sq = math.sqrt(years)
    with np.errstate(divide="ignore"):
        d1 = (np.log(spot / strike) + (rate - q + vol * vol / 2) * years) / (vol * sq)
    d2 = d1 - vol * sq
    df_q, df_r = math.exp(-q * years), math.exp(-rate * years)
    if call:
        return spot * df_q * ndtr(d1) - strike * df_r * ndtr(d2)
    return strike * df_r * ndtr(-d2) - spot * df_q * ndtr(-d1)


@dataclass(frozen=True)
class SimInput:
    spot: float
    strike: float
    call: bool
    iv: float               # annualised decimal, used for repricing
    path_vol: float         # annualised decimal on the trading-time basis
    years_to_expiry: float  # calendar ACT/365
    horizon_minutes: int
    entry_premium: float    # modelled mid premium at entry (repriced at t = 0)
    target_pct: float
    stop_pct: float
    rate: float = 0.065
    q: float = 0.0
    iv_shock_pts: float = 0.0
    paths: int = 4000
    steps: int | None = None
    seed: int = 7


def simulate(p: SimInput) -> dict[str, Any]:
    steps = p.steps or max(10, min(p.horizon_minutes, 240))
    n = p.paths
    rng = np.random.default_rng(p.seed)
    dt_trade = p.horizon_minutes / TRADING_MINUTES_PER_YEAR / steps
    dt_cal = p.horizon_minutes / CALENDAR_MINUTES_PER_YEAR / steps
    iv = max(p.iv + p.iv_shock_pts / 100, 1e-4)
    # Premium at t = 0 under the (possibly shocked) IV, so an IV shock is an
    # instantaneous mark-to-model change at entry, measured against entry_premium.
    target = p.entry_premium * (1 + p.target_pct / 100)
    stop = p.entry_premium * (1 - p.stop_pct / 100)
    s = np.full(n, float(p.spot))
    alive = np.ones(n, dtype=bool)
    exit_prem = np.full(n, np.nan)
    outcome = np.zeros(n, dtype=np.int8)  # 0 neither, 1 target, -1 stop
    first_step = np.full(n, -1)
    sig = p.path_vol
    prem = bs_price(s, p.strike, p.years_to_expiry, iv, p.rate, p.q, p.call)
    if p.iv_shock_pts:
        hit_t, hit_s = prem >= target, prem <= stop
        outcome[hit_t], outcome[hit_s & ~hit_t] = 1, -1
        exit_prem[hit_t | hit_s] = prem[hit_t | hit_s]
        first_step[hit_t | hit_s] = 0
        alive &= ~(hit_t | hit_s)
    for k in range(1, steps + 1):
        z = rng.standard_normal(n)
        s = s * np.exp(-0.5 * sig * sig * dt_trade + sig * math.sqrt(dt_trade) * z)
        t_left = max(p.years_to_expiry - k * dt_cal, 0.0)
        prem = bs_price(s, p.strike, t_left, iv, p.rate, p.q, p.call)
        hit_t = alive & (prem >= target)
        hit_s = alive & (prem <= stop)
        outcome[hit_t] = 1
        outcome[hit_s] = -1
        exit_prem[hit_t] = target  # a resting target fills at the target
        exit_prem[hit_s] = prem[hit_s]  # a stop fills where the price is (gap risk)
        first_step[hit_t | hit_s] = k
        alive &= ~(hit_t | hit_s)
        if not alive.any():
            break
    exit_prem[alive] = prem[alive]
    pnl = exit_prem - p.entry_premium

    def prob(mask: np.ndarray) -> dict[str, float]:
        v = float(mask.mean())
        return {"p": round(v, 4), "std_error": round(math.sqrt(v * (1 - v) / n), 4)}

    qs = np.quantile(pnl, [0.05, 0.25, 0.5, 0.75, 0.95])
    hit = first_step[first_step > 0]
    return {
        "p_target_first": prob(outcome == 1),
        "p_stop_first": prob(outcome == -1),
        "p_neither": prob(outcome == 0),
        "target_premium": round(target, 4), "stop_premium": round(stop, 4),
        "expected_exit_premium": round(float(exit_prem.mean()), 4),
        "expected_premium_pnl_per_unit": round(float(pnl.mean()), 4),
        "premium_pnl_quantiles_per_unit": dict(zip(
            ("p05", "p25", "p50", "p75", "p95"), (round(float(x), 4) for x in qs),
            strict=True)),
        "median_minutes_to_exit": round(float(np.median(hit)) * p.horizon_minutes / steps, 1)
        if hit.size else None,
        "paths": n, "steps": steps, "seed": p.seed,
    }
