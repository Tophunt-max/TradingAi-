"""Options intelligence for buying CE/PE contracts (research only, never an order).

For every candidate contract four outputs are kept separate:

1. underlying_direction - P(UP/DOWN/SIDEWAYS) from a PROMOTED model, else
   null with the reason. Never estimated here.
2. premium_path - P(premium hits target before stop) from the documented
   Monte Carlo in app.options.simulation (MODEL_ESTIMATE).
3. expected_net - E[premium P&L] from the same simulation minus spread
   crossing, slippage and statutory charges (MODEL_ESTIMATE).
4. risks - theta, spread, vega, gamma, liquidity, expiry and affordability.

A correct direction view can still lose money on an option; that is why the
outputs are not combined into one score. Contracts, expiries, lot sizes and
Greeks come only from the chain. Options buying only.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from app.analysis.context import Ctx
from app.execution import charges as CH
from app.options import simulation as SIM
from app.options import volatility as VOL
from app.options.greeks import black_scholes

RATE = 0.065  # same r as the demo chain's Black-Scholes model
DIV_YIELD = 0.0
EXPIRY_TIME_UTC = time(10, 0)  # 15:30 IST
MAX_SPREAD_PCT = 2.0
MIN_OI = 1000
MIN_VOLUME = 100
NEAR_EXPIRY_DAYS = 2.0
HIGH_THETA_PCT = 5.0  # theta per day as % of premium
IV_SHOCKS = (-2.0, 2.0)
DEFAULT_SLIPPAGE_TICKS = 1
LABEL = "MODEL_ESTIMATE"


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def years_to_expiry(expiry: date, now: datetime) -> float:
    exp = datetime.combine(expiry, EXPIRY_TIME_UTC, UTC)
    return max((exp - now).total_seconds(), 0.0) / (365 * 24 * 3600)


def moneyness(strike: float, spot: float, side: str, is_atm: bool) -> str:
    if is_atm:
        return "ATM"
    itm = strike < spot if side == "CE" else strike > spot
    return "ITM" if itm else "OTM"


def underlying_context(ctx: Ctx | None) -> dict[str, Any]:
    if ctx is None or not len(ctx):
        return {"available": False, "reason": "no candles"}
    s = ctx.s
    c, e50, st = ctx.c[-1], s["ema50"][-1], s["supertrend_dir"][-1]
    return {
        "available": True, "close": round(c, 2), "timeframe": ctx.timeframe,
        "price_vs_ema50": None if e50 is None else ("ABOVE" if c > e50 else "BELOW"),
        "supertrend": {1: "UP", -1: "DOWN"}.get(st) if st is not None else None,
        "hv20_annualised": None if s["hv20"][-1] is None else round(s["hv20"][-1], 4),
        "realized_vol20": None if s["rv20"][-1] is None else round(s["rv20"][-1], 4),
        "note": "HV is annualised over 252 sessions × 375 minutes (trading time).",
    }


def _greeks(leg: dict[str, Any], *, spot: float, strike: float, years: float, call: bool,
            provider_greeks: bool) -> dict[str, Any]:
    keys = ("delta", "gamma", "theta", "vega")
    have = all(leg.get(k) is not None for k in keys)
    if have:
        return {**{k: float(leg[k]) for k in keys},
                "source": "PROVIDER" if provider_greeks else "MODEL",
                "note": None if provider_greeks else
                "Chain Greeks are model values (Black-Scholes on the chain IV)."}
    iv = _f(leg.get("iv"))
    if iv is None or iv <= 0:
        return {**{k: None for k in keys}, "source": None,
                "note": "No Greeks and no IV to compute them."}
    g = black_scholes(spot=spot, strike=strike, years=years, vol=iv / 100, rate=RATE, call=call)
    return {"delta": g.delta, "gamma": g.gamma, "theta": g.theta, "vega": g.vega,
            "source": "MODEL", "note": "Black-Scholes from chain IV (see assumptions)."}


def _charges(buy: float, sell: float, qty: int, day: date, brokerage: Decimal) -> float:
    rt = CH.round_trip(buy=Decimal(str(round(buy, 2))), sell=Decimal(str(round(max(sell, 0.05),
                                                                            2))),
                       qty=qty, day=day, brokerage=brokerage)
    return float(rt["total"]["total"])


def evaluate_contract(*, row: dict[str, Any], side: str, spot: float, expiry: date,
                      now: datetime, lot: int, tick: float, provider_greeks: bool,
                      path_vol: float | None, path_vol_source: str, stop_pct: float,
                      target_pct: float, horizon_minutes: int, brokerage: Decimal,
                      slippage_ticks: int, available_funds: float | None,
                      risk_per_trade: float | None, direction: dict[str, Any],
                      paths: int, seed: int) -> dict[str, Any] | None:
    leg = row.get("call" if side == "CE" else "put")
    if not leg:
        return None
    call = side == "CE"
    strike = float(row["strike"])
    ltp, bid, ask = _f(leg.get("ltp")), _f(leg.get("bid")), _f(leg.get("ask"))
    iv = _f(leg.get("iv"))
    years = years_to_expiry(expiry, now)
    mins_left = years * SIM.CALENDAR_MINUTES_PER_YEAR
    horizon = int(min(horizon_minutes, max(mins_left, 0)))
    g = _greeks(leg, spot=spot, strike=strike, years=years, call=call,
                provider_greeks=provider_greeks)
    mid = (bid + ask) / 2 if bid and ask else ltp
    spread = (ask - bid) if bid and ask else None
    spread_pct = spread / mid * 100 if spread is not None and mid else None
    flags: list[dict[str, str]] = []
    blocked: list[str] = []

    def flag(code: str, msg: str, block: bool = False) -> None:
        flags.append({"code": code, "message": msg})
        if block:
            blocked.append(msg)

    if not (bid and ask):
        flag("NO_BID_ASK", "Bid or ask missing: a market buy would be rejected.", True)
    elif spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
        flag("WIDE_SPREAD", f"Spread {spread_pct:.2f}% of premium exceeds {MAX_SPREAD_PCT}%.")
    if (leg.get("oi") or 0) < MIN_OI:
        flag("LOW_OI", f"Open interest below {MIN_OI}.")
    if (leg.get("volume") or 0) < MIN_VOLUME:
        flag("LOW_VOLUME", f"Volume below {MIN_VOLUME}.")
    days = years * 365
    if days < NEAR_EXPIRY_DAYS:
        flag("NEAR_EXPIRY", f"{days:.1f} days to expiry: time decay accelerates.")
    if g["theta"] is not None and mid and abs(g["theta"]) / mid * 100 > HIGH_THETA_PCT:
        flag("HIGH_THETA", f"Theta is {abs(g['theta']) / mid * 100:.1f}% of premium per day.")
    if iv is None or iv <= 0:
        flag("NO_IV", "No implied volatility: premium path cannot be modelled.", True)
    if horizon < 1:
        flag("EXPIRED", "Contract is at or past expiry.", True)

    slip = slippage_ticks * tick
    entry_fill = (ask or 0) + slip
    premium_per_lot = entry_fill * lot if ask else None
    est_entry_charges = _charges(entry_fill, entry_fill * (1 - stop_pct / 100), lot, now.date(),
                                 brokerage) if ask else None
    loss_at_stop = (entry_fill * stop_pct / 100 * lot + (spread or 0) * lot + slip * lot
                    + (est_entry_charges or 0)) if ask else None
    affordability: dict[str, Any] = {"premium_per_lot": _r(premium_per_lot),
                                     "available_funds": available_funds,
                                     "risk_per_trade_limit": risk_per_trade,
                                     "estimated_loss_at_stop": _r(loss_at_stop)}
    if premium_per_lot is not None and available_funds is not None:
        affordability["affordable"] = premium_per_lot + (est_entry_charges or 0) \
            <= available_funds
        if not affordability["affordable"]:
            flag("UNAFFORDABLE", "Premium for one lot plus charges exceeds available funds.", True)
    else:
        affordability["affordable"] = None
    if loss_at_stop is not None and risk_per_trade is not None:
        affordability["within_risk_per_trade"] = loss_at_stop <= risk_per_trade
        if not affordability["within_risk_per_trade"]:
            flag("RISK_ABOVE_LIMIT", f"Estimated loss at stop ₹{loss_at_stop:.2f} exceeds the "
                 f"₹{risk_per_trade:.2f} risk-per-trade limit.", True)

    premium_path: dict[str, Any] = {"label": LABEL, "available": False}
    expected_net: dict[str, Any] = {"label": LABEL, "available": False}
    vol_for_paths = path_vol if path_vol else (iv / 100 if iv else None)
    if iv and iv > 0 and horizon >= 1 and vol_for_paths and mid:
        model_entry = float(SIM.bs_price(spot, strike, years, iv / 100, RATE, DIV_YIELD,
                                         call))
        if model_entry < 0.05:
            premium_path["reason"] = "Modelled premium is below one tick."
        else:
            base = SIM.SimInput(spot=spot, strike=strike, call=call, iv=iv / 100,
                                path_vol=vol_for_paths, years_to_expiry=years,
                                horizon_minutes=horizon, entry_premium=model_entry,
                                target_pct=target_pct, stop_pct=stop_pct, rate=RATE,
                                q=DIV_YIELD, paths=paths, seed=seed)
            sim = SIM.simulate(base)
            scen = []
            for shock in IV_SHOCKS:
                s2 = SIM.simulate(replace(base, iv_shock_pts=shock))
                scen.append({"iv_shock_pts": shock,
                             "p_target_first": s2["p_target_first"]["p"],
                             "p_stop_first": s2["p_stop_first"]["p"],
                             "expected_premium_pnl_per_unit": s2[
                                 "expected_premium_pnl_per_unit"]})
            premium_path = {
                "label": LABEL, "available": True, "method": SIM.METHOD,
                "model_entry_premium": round(model_entry, 4), "market_mid": round(mid, 4),
                "target_pct": target_pct, "stop_pct": stop_pct, "horizon_minutes": horizon,
                "path_volatility": round(vol_for_paths, 4),
                "path_volatility_source": path_vol_source if path_vol else "IMPLIED",
                **{k: sim[k] for k in ("p_target_first", "p_stop_first", "p_neither",
                                       "median_minutes_to_exit", "paths", "steps", "seed")},
                "iv_scenarios": scen,
            }
            per_unit = sim["expected_premium_pnl_per_unit"]
            gross = per_unit * lot
            spread_cost = (spread or 0) * lot
            slip_cost = 2 * slip * lot
            exit_mid = mid + sim["expected_exit_premium"] - model_entry
            chg = _charges(entry_fill, exit_mid - (spread or 0) / 2 - slip, lot, now.date(),
                           brokerage)
            total = spread_cost + slip_cost + chg
            q = sim["premium_pnl_quantiles_per_unit"]
            expected_net = {
                "label": LABEL, "available": True, "qty": lot,
                "expected_premium_pnl": round(gross, 2),
                "costs": {"spread_crossing": round(spread_cost, 2),
                          "slippage": round(slip_cost, 2), "charges": round(chg, 2),
                          "total": round(total, 2),
                          "slippage_ticks_each_side": slippage_ticks},
                "expected_net_pnl": round(gross - total, 2),
                "net_pnl_quantiles": {k: round(v * lot - total, 2) for k, v in q.items()},
                "note": "Mid-to-mid premium change from the simulation, minus the full spread "
                        "(half at entry, half at exit), slippage both ways and statutory "
                        "charges (CH.round_trip).",
            }
    else:
        premium_path["reason"] = ("No IV, mid price or horizon left to model the premium."
                                  if not blocked else blocked[0])
        expected_net["reason"] = premium_path["reason"]

    risks = {
        "theta_per_day_lot": _r(g["theta"] * lot) if g["theta"] is not None else None,
        "spread_pct": _r(spread_pct), "spread_cost_lot": _r(spread * lot) if spread else None,
        "vega_per_vol_pt_lot": _r(g["vega"] * lot) if g["vega"] is not None else None,
        "iv_move_pnl_lot": {f"{s:+.0f}": _r(g["vega"] * s * lot) for s in IV_SHOCKS}
        if g["vega"] is not None else None,
        "gamma": g["gamma"],
        "gamma_pnl_1pct_move_lot": _r(0.5 * g["gamma"] * (0.01 * spot) ** 2 * lot)
        if g["gamma"] is not None else None,
        "flags": flags,
    }
    dir_probs = direction.get("probabilities")
    return {
        "strike": strike, "side": side,
        "moneyness": moneyness(strike, spot, side, bool(row.get("is_atm"))),
        "trading_symbol": leg.get("trading_symbol"), "security_id": leg.get("security_id"),
        "ltp": ltp, "bid": bid, "ask": ask, "mid": _r(mid, 4), "iv": iv,
        "oi": leg.get("oi"), "oi_change": leg.get("oi_change"), "volume": leg.get("volume"),
        "greeks": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in g.items()},
        "underlying_direction": {
            **direction,
            "relevant_probability": None if not dir_probs else
            dir_probs.get("UP" if call else "DOWN"),
            "relevant_outcome": "UP" if call else "DOWN"},
        "premium_path": premium_path,
        "expected_net": expected_net,
        "risks": risks,
        "affordability": affordability,
        "eligible": not blocked,
        "blocked_reasons": blocked,
    }


def _r(v: float | None, d: int = 2) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, d)


def analyse(*, chain: dict[str, Any], source: str, quality_status: str, side: str,
            now: datetime, strikes: int = 2, strike: float | None = None,
            stop_pct: float = 20.0, target_pct: float = 30.0, horizon_minutes: int = 60,
            ctx: Ctx | None = None, direction: dict[str, Any] | None = None,
            available_funds: float | None = None, risk_per_trade: float | None = None,
            brokerage: Decimal = Decimal("20"), demo_lot_size: int = 1,
            slippage_ticks: int = DEFAULT_SLIPPAGE_TICKS,
            other_chains: list[dict[str, Any]] | None = None,
            iv_history: list[float] | None = None, paths: int = 4000,
            seed: int = 7) -> dict[str, Any]:
    rows = chain.get("rows") or []
    demo = source == "DEMO"
    spot = _f(chain.get("spot"))
    lot = chain.get("lot_size") or (demo_lot_size if demo else None)
    lot_source = ("INSTRUMENT_MASTER" if chain.get("lot_size") else
                  "DEMO_PLACEHOLDER" if demo else None)
    tick = chain.get("tick_size") or (0.05 if demo else None)
    expiry = chain["expiry"]
    if isinstance(expiry, str):
        expiry = date.fromisoformat(expiry)
    provider_greeks = str(chain.get("greeks_model", "")).lower().startswith("provided")
    direction = direction or {"probabilities": None,
                              "reason": "No promoted model for this instrument and timeframe."}
    uctx = underlying_context(ctx)
    path_vol = uctx.get("hv20_annualised") if uctx.get("available") else None
    out: dict[str, Any] = {
        "underlying": chain.get("underlying"), "expiry": expiry, "side": side,
        "spot": spot, "atm_strike": chain.get("atm_strike"),
        "data_source": "SIMULATED" if demo else source, "source": source,
        "quality": quality_status,
        "time_to_expiry": {"days": round(years_to_expiry(expiry, now) * 365, 3),
                           "years": round(years_to_expiry(expiry, now), 6),
                           "day_count": "ACT/365 calendar, expiry 15:30 IST"},
        "lot_size": lot, "lot_size_source": lot_source,
        "tick_size": tick, "tick_size_source": "INSTRUMENT_MASTER" if chain.get("tick_size")
        else "DEMO_PLACEHOLDER" if demo else None,
        "greeks_source": "PROVIDER" if provider_greeks else "MODEL",
        "greeks_model": chain.get("greeks_model"),
        "underlying_direction": direction,
        "underlying_context": uctx,
        "iv_context": {
            "atm_iv": VOL.atm_iv(rows),
            **VOL.iv_rank(iv_history, VOL.atm_iv(rows)),
            "skew": VOL.skew(rows),
            "term_structure": VOL.term_structure([chain, *(other_chains or [])]),
        },
        "futures_basis": {"value": None,
                          "reason": "No futures quote is available from the market-data "
                                    "provider interface."},
        "candidates": [],
        "notes": [],
    }
    if demo:
        out["notes"].append("DEMO data is SIMULATED. Lot size 1 and tick 0.05 are labelled "
                            "placeholders, not NSE contract metadata.")
    if side not in ("CE", "PE"):
        raise ValueError("side must be CE or PE")
    if spot is None or not rows:
        out["notes"].append("Chain has no spot price or strikes.")
        return out | {"assumptions": assumptions(path_vol, horizon_minutes, slippage_ticks)}
    if not lot or not tick:
        out["notes"].append("Lot size or tick size is not available from the instrument "
                            "master, so no contract can be evaluated.")
        return out | {"assumptions": assumptions(path_vol, horizon_minutes, slippage_ticks)}
    if quality_status == "FAIL":
        out["notes"].append("Option-chain quality check failed; outputs are not reliable.")
    atm = next((i for i, r in enumerate(rows) if r.get("is_atm")), None)
    if strike is not None:
        pick = [r for r in rows if abs(float(r["strike"]) - float(strike)) < 1e-6]
        if not pick:
            out["notes"].append("Strike not in the chain.")
    elif atm is None:
        pick = []
        out["notes"].append("No ATM strike in the chain.")
    else:
        pick = rows[max(0, atm - strikes):atm + strikes + 1]
    for r in pick:
        c = evaluate_contract(
            row=r, side=side, spot=spot, expiry=expiry, now=now, lot=int(lot),
            tick=float(tick), provider_greeks=provider_greeks, path_vol=path_vol,
            path_vol_source="REALIZED_HV20", stop_pct=stop_pct, target_pct=target_pct,
            horizon_minutes=horizon_minutes, brokerage=brokerage, slippage_ticks=slippage_ticks,
            available_funds=available_funds, risk_per_trade=risk_per_trade, direction=direction,
            paths=paths, seed=seed)
        if c is not None:
            c["lot_size_source"] = lot_source
            out["candidates"].append(c)

    def key(c: dict[str, Any]) -> tuple[int, float]:
        en = c["expected_net"].get("expected_net_pnl")
        return (0 if c["eligible"] else 1, -(en if en is not None else -1e18))

    out["candidates"].sort(key=key)
    for i, c in enumerate(out["candidates"], 1):
        c["rank"] = i
    out["ranking"] = ("Eligible contracts first, then by modelled expected net P&L. A ranking "
                      "is not a recommendation; the independent risk manager decides.")
    out["assumptions"] = assumptions(path_vol, horizon_minutes, slippage_ticks)
    return out


def assumptions(path_vol: float | None, horizon: int, slippage_ticks: int) -> dict[str, Any]:
    return {
        "pricing_model": "Black-Scholes-Merton, European exercise",
        "risk_free_rate": RATE, "dividend_yield": DIV_YIELD,
        "day_count": "ACT/365 calendar for time to expiry and decay",
        "underlying_process": "Driftless GBM (no directional edge assumed)",
        "path_volatility": "Realized HV20 of the underlying (trading-time annualised)"
        if path_vol else "Contract IV used on a trading-time basis (no realized vol)",
        "implied_volatility": "Held constant over the horizon; ±2 vol-point shock scenarios",
        "horizon_minutes": horizon,
        "monitoring": "Discrete steps (≤ 1 per minute); stops can gap through",
        "fills": "Entry at ask + slippage, exit at mid − half spread − slippage; "
                 f"{slippage_ticks} tick(s) slippage each side",
        "not_modelled": ["IV smile dynamics", "spread widening", "overnight gaps",
                         "early exercise", "liquidity impact of the order size"],
    }
