"""Request orchestration for the Backtesting Lab API.

Builds engine inputs from a request (catalogue strategy or the user's bot),
enforces the user's global risk limits, loads closed candles from the
configured provider, runs the engine and labels the result.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import context as C
from app.backtesting import cache
from app.backtesting import engine as E
from app.backtesting import metrics as M
from app.backtesting import strategies as S
from app.backtesting import validation as V
from app.backtesting.data import fingerprint, session_bars
from app.backtesting.schemas import (
    BacktestRequest,
    MonteCarloRequest,
    SensitivityRequest,
    WalkForwardRequest,
)
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.market_data.factory import provider_errors
from app.market_data.provider import UNIVERSE, MarketDataProvider
from app.models.bots import Bot, BotConfigVersion, RiskSettings
from app.schemas.bots import BotConfig
from app.strategies.catalog import STRATEGIES
from app.strategies.conditions import describe

SIMULATED_WARNING = "Simulated data: results are not evidence of real performance"
FIDELITY_NOTE = "incomplete proxy for options: ignores premium, IV, theta, spread"
OPTIONS_UNAVAILABLE = ("Historical option premiums are not available from the configured data "
                       "source")
TIME_BUDGET_SECONDS = 10.0
MIN_BARS = 150
DEMO_RAW_FACTOR = 5.6  # 24x7 DEMO bars per NSE session bar (1440/375 min x 7/5 days)
OHLC = ("open", "high", "low", "close")


def _invalid(message: str, code: str = "VALIDATION_ERROR", details: Any = None) -> AppError:
    return AppError(message, code=code, status_code=422, details=details)


@dataclass
class Setup:
    strategy: E.Strategy
    exe: E.Execution
    risk: E.Risk
    underlying: str
    timeframe: str
    meta: dict[str, Any]
    limits: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def labels(source: str) -> dict[str, Any]:
    warnings = [f"UNDERLYING_PROXY: {FIDELITY_NOTE}."]
    if source == "DEMO":
        warnings.insert(0, SIMULATED_WARNING)
    return {"fidelity": E.FIDELITY_PROXY, "fidelity_note": FIDELITY_NOTE, "data_source": source,
            "evidence": source != "DEMO", "warnings": warnings}


def _bot_config(db: Session, user_id: Any, bot_id: Any) -> BotConfig:
    bot = db.get(Bot, bot_id)
    if bot is None or bot.user_id != user_id:
        raise NotFoundError("Bot not found.")
    ver = db.scalar(select(BotConfigVersion).where(BotConfigVersion.bot_id == bot.id,
                                                   BotConfigVersion.version
                                                   == bot.current_version))
    if ver is None:
        raise NotFoundError("Bot configuration not found.")
    return BotConfig.model_validate(ver.config)


def _limits(rs: RiskSettings, body: BacktestRequest, cfg: BotConfig | None,
            ) -> dict[str, Any]:
    g = {"risk_per_trade": float(rs.max_risk_per_trade),
         "daily_loss_limit": float(rs.daily_loss_limit),
         "daily_profit_lock": float(rs.daily_profit_lock),
         "max_entries_per_day": int(rs.max_entries_per_day),
         "lots": int(rs.max_lots_per_trade)}
    eff: dict[str, Any] = {**g, "lots": 1, "re_entry_after_stop_loss": rs.re_entry_after_stop_loss,
                           "square_off_time_ist": rs.square_off_time_ist,
                           "session_start_ist": "09:20", "session_end_ist": "15:00",
                           "cooldown_minutes": 15, "capital": float(rs.capital),
                           "exit_on_loss_limit": rs.exit_on_loss_limit}
    if cfg is not None:
        eff.update(risk_per_trade=min(g["risk_per_trade"], float(cfg.risk.risk_per_trade)),
                   max_entries_per_day=min(g["max_entries_per_day"],
                                           cfg.risk.max_entries_per_day),
                   re_entry_after_stop_loss=cfg.risk.re_entry_after_stop_loss
                   and rs.re_entry_after_stop_loss,
                   square_off_time_ist=min(cfg.exit.square_off_time_ist, rs.square_off_time_ist),
                   session_start_ist=cfg.session.start_ist, session_end_ist=cfg.session.end_ist,
                   cooldown_minutes=cfg.session.cooldown_minutes)
    o = body.risk
    problems = []
    for name in ("risk_per_trade", "daily_loss_limit", "daily_profit_lock",
                 "max_entries_per_day", "lots"):
        v = getattr(o, name)
        if v is None:
            continue
        if v > g[name]:
            problems.append(f"{name} {v} exceeds your global limit {g[name]}.")
        else:
            eff[name] = v
    if o.re_entry_after_stop_loss is not None:
        if o.re_entry_after_stop_loss and not rs.re_entry_after_stop_loss:
            problems.append("Re-entry after a stop-loss is disabled in your global limits.")
        else:
            eff["re_entry_after_stop_loss"] = o.re_entry_after_stop_loss
    if o.square_off_time_ist is not None:
        if o.square_off_time_ist > rs.square_off_time_ist:
            problems.append(f"Square-off {o.square_off_time_ist} is later than your global "
                            f"square-off {rs.square_off_time_ist}.")
        else:
            eff["square_off_time_ist"] = o.square_off_time_ist
    for name in ("session_start_ist", "session_end_ist", "cooldown_minutes"):
        if getattr(o, name) is not None:
            eff[name] = getattr(o, name)
    if eff["session_start_ist"] >= eff["session_end_ist"]:
        problems.append("Session start must be before session end.")
    if problems:
        raise _invalid("Risk overrides must not exceed your global risk limits.",
                       "RISK_OVERRIDE_EXCEEDS_LIMIT", problems)
    return eff


def build_setup(body: BacktestRequest, db: Session, user_id: Any, rs: RiskSettings,
                settings: Settings) -> Setup:
    if body.fidelity != E.FIDELITY_PROXY:
        raise _invalid(OPTIONS_UNAVAILABLE, "OPTION_HISTORY_UNAVAILABLE",
                       {"supported_fidelity": [E.FIDELITY_PROXY]})
    cfg = _bot_config(db, user_id, body.bot_id) if body.bot_id else None
    notes: list[str] = []
    if cfg is not None:
        sid, underlying, timeframe = cfg.strategy_id, cfg.instrument.underlying, cfg.timeframe
        side, logic = cfg.instrument.option_type, cfg.entry.logic
        conds = [c.model_dump() for c in cfg.entry.conditions]
        if not conds:
            raise _invalid("This bot has no entry conditions to backtest.")
        notes.append("The bot's stop-loss and target are set on option premium, which the "
                     "underlying proxy cannot model; the request's underlying-level stop and "
                     "target are used instead.")
        f = cfg.filters
        min_rvol, max_atr = f.min_relative_volume, f.max_atr_pct
    else:
        sid = body.strategy_id or ""
        underlying = (body.underlying or "NIFTY").upper()
        timeframe = body.timeframe or "5m"
        side, logic = body.side or "CE", body.entry_logic
        given = [c.model_dump() for c in body.entry_conditions] if body.entry_conditions else None
        try:
            conds = S.resolve(sid, side, given)
        except ValueError as exc:
            raise _invalid(str(exc), "STRATEGY_NOT_BACKTESTABLE") from exc
        min_rvol = max_atr = None
    if underlying not in UNIVERSE:
        raise _invalid(f"Unsupported underlying '{underlying}'.", details=UNIVERSE)
    problems = S.check_conditions(conds)
    if problems:
        raise _invalid("Some entry conditions cannot be backtested.", "UNSUPPORTED_CONDITION",
                       problems)
    strat = E.Strategy(conditions=conds, logic=logic, side=side,
                       stop=E.Level(body.stop.type, body.stop.value),
                       target=E.Level(body.target.type, body.target.value),
                       min_relative_volume=min_rvol, max_atr_pct=max_atr,
                       regimes=tuple(STRATEGIES[sid]["regimes"]) if body.regime_filter
                       and "ANY" not in STRATEGIES[sid]["regimes"] else None)
    if body.threshold is not None:
        if V.threshold_index(strat) is None:
            raise _invalid("This strategy has no numeric threshold to set.")
        strat = _set_threshold(strat, body.threshold)
    lim = _limits(rs, body, cfg)
    c = body.costs
    exe = E.Execution(
        entry_fill=c.entry_fill, stop_fill=c.stop_fill, latency_bars=c.latency_bars,
        spread_bps=c.spread_bps, slippage_bps=c.slippage_bps, slippage_ticks=c.slippage_ticks,
        tick_size=c.tick_size, reject_probability=c.reject_probability,
        partial_fill_probability=c.partial_fill_probability, min_volume=c.min_volume,
        brokerage_per_order=float(settings.paper_brokerage_per_order
                                  if c.brokerage_per_order is None else c.brokerage_per_order),
        charge_premium=c.charge_premium, charge_premium_pct=c.charge_premium_pct)
    risk = E.Risk(capital=lim["capital"], risk_per_trade=lim["risk_per_trade"],
                  daily_loss_limit=lim["daily_loss_limit"],
                  daily_profit_lock=lim["daily_profit_lock"],
                  max_entries_per_day=lim["max_entries_per_day"], lots=lim["lots"],
                  lot_size=body.lot_size,
                  re_entry_after_stop_loss=lim["re_entry_after_stop_loss"],
                  exit_on_loss_limit=lim["exit_on_loss_limit"],
                  square_off_ist=lim["square_off_time_ist"],
                  session_start_ist=lim["session_start_ist"],
                  session_end_ist=lim["session_end_ist"],
                  cooldown_minutes=lim["cooldown_minutes"])
    meta_s = STRATEGIES[sid]
    if timeframe not in meta_s["timeframes"]:
        notes.append(f"{meta_s['name']} is designed for {', '.join(meta_s['timeframes'])}; "
                     f"this run uses {timeframe}.")
    if body.lot_size == 1:
        notes.append("Lot size 1 is a placeholder (1 unit per lot), not the exchange lot size. "
                     "Set lot_size to the contract's lot size for rupee-accurate results.")
    meta = {"id": sid, "name": meta_s["name"], "version": meta_s["version"],
            "bot_id": str(body.bot_id) if body.bot_id else None, "side": side,
            "direction": "LONG underlying" if side == "CE" else "SHORT underlying",
            "logic": logic, "conditions": [describe(x) for x in strat.conditions],
            "stop": body.stop.model_dump(), "target": body.target.model_dump(),
            "regime_filter": list(strat.regimes) if strat.regimes else None}
    return Setup(strat, exe, risk, underlying, timeframe, meta, lim, notes)


def _set_threshold(strat: E.Strategy, value: float) -> E.Strategy:
    k = V.threshold_index(strat)
    conds = [dict(c) for c in strat.conditions]
    conds[k]["value"] = value  # type: ignore[index]
    return replace(strat, conditions=conds)


def load_bars(provider: MarketDataProvider, underlying: str, timeframe: str,
              bars: int) -> tuple[E.Prepared, dict[str, Any]]:
    demo = provider.name == "DEMO"
    limit = min(int(bars * DEMO_RAW_FACTOR) + 400, 20_000) if demo else bars
    with provider_errors():
        r = provider.candles(underlying, timeframe, limit)
    rows = [k for k in r.data if all(k.get(f) is not None for f in OHLC)]
    missing = len(r.data) - len(rows)
    sess = session_bars(rows)
    outside = len(rows) - len(sess)
    sess = sess[-bars:]
    if len(sess) < MIN_BARS:
        raise _invalid(f"Only {len(sess)} closed session candles are available; at least "
                       f"{MIN_BARS} are needed.", "INSUFFICIENT_DATA")
    fp = fingerprint(sess)
    key = cache.request_hash([provider.name, r.source, underlying, timeframe, fp])
    prep = cache.PREPARED.get(key)
    if prep is None:
        prep = E.Prepared(C.build(sess, intraday=True))
        cache.PREPARED.put(key, prep)
    info = {"provider": r.provider, "source": r.source, "as_of": r.as_of.isoformat(),
            "bars": len(sess), "from": E._iso(sess[0]["ts"]), "to": E._iso(sess[-1]["ts"]),
            "fingerprint": fp, "dropped_missing_ohlc": missing,
            "dropped_outside_session": outside, "quality": r.quality.status,
            "session_filter": "Weekday bars starting 09:15-15:30 IST"}
    return prep, info


def _assumptions(st: Setup) -> dict[str, Any]:
    return {**asdict(st.exe), "lot_size": st.risk.lot_size, "lots": st.risk.lots,
            "charges": "app.execution.charges dated schedules (STT 0.15% on the sell side from "
                       "2026-04-01) applied to an ASSUMED option premium per unit: "
                       "charge_premium, else charge_premium_pct of the underlying entry price.",
            "spread": "Candles carry no bid/ask: the spread is the configured assumption.",
            "structure_window_bars": E.EVAL_WINDOW, "bot_window_bars": E.BOT_WINDOW}


def _context(body: BacktestRequest, db: Session, user_id: Any, rs: RiskSettings,
             provider: MarketDataProvider, settings: Settings,
             ) -> tuple[Setup, E.Prepared, dict[str, Any]]:
    st = build_setup(body, db, user_id, rs, settings)
    prep, info = load_bars(provider, st.underlying, st.timeframe, body.bars)
    return st, prep, info


def _header(st: Setup, info: dict[str, Any], key: str, seed: int) -> dict[str, Any]:
    lab = labels(info["source"])
    lab["warnings"] = lab["warnings"] + st.notes
    return {**lab, "request_hash": key, "cached": False, "seed": seed, "strategy": st.meta,
            "underlying": st.underlying, "timeframe": st.timeframe, "data": info,
            "assumptions": _assumptions(st), "risk_limits": st.limits}


def _curve(equity: list[tuple[Any, float]]) -> list[dict[str, Any]]:
    return [{"t": E._iso(t), "equity": round(e, 2)} for t, e in M.downsample(equity, 200)]


def _brief(s: dict[str, Any]) -> dict[str, Any]:
    return V._brief(s)


def run(body: BacktestRequest, db: Session, user_id: Any, rs: RiskSettings,
        provider: MarketDataProvider, settings: Settings) -> tuple[dict[str, Any], list[float]]:
    t0 = time.monotonic()
    st, prep, info = _context(body, db, user_id, rs, provider, settings)
    key = cache.request_hash(["run", body.model_dump(mode="json"), st.limits,
                              info["fingerprint"]])
    hit = cache.RESULTS.get(key)
    if hit is not None:
        return {**hit[0], "cached": True}, hit[1]
    with prep.lock:
        raw = E.run(prep, st.strategy, st.exe, st.risk, seed=body.seed)
    summary = E.summary(raw)
    by_tf = {st.timeframe: _brief(summary)}
    for tf in body.compare_timeframes:
        if tf in by_tf:
            continue
        p2, _ = load_bars(provider, st.underlying, tf, body.bars)
        with p2.lock:
            by_tf[tf] = _brief(E.summary(E.run(p2, st.strategy, st.exe, st.risk,
                                               seed=body.seed)))
    report = {
        **_header(st, info, key, body.seed),
        "metrics": summary,
        "by_regime": M.group_summary(raw["trades"], lambda t: t["regime"]),
        "by_timeframe": by_tf,
        "equity_curve": _curve(raw["equity"]),
        "trades": raw["trades"][:1000],
        "trades_truncated": len(raw["trades"]) > 1000,
        "suppressed": raw["suppressed"], "rejected": raw["rejected"],
        "engine_warnings": raw["warnings"],
        "computed_ms": round((time.monotonic() - t0) * 1000),
    }
    nets = [t["net"] for t in raw["trades"]]
    cache.RESULTS.put(key, (report, nets))
    return report, nets


def _grid(g: Any) -> V.Grid:
    return V.Grid(tuple(g.stops_pct), tuple(g.targets_pct),
                  tuple(g.thresholds) if g.thresholds else None)


def _validation_errors(fn: Any) -> Any:
    try:
        return fn()
    except V.BudgetExceeded as exc:
        raise _invalid(str(exc), "COMPUTE_BUDGET_EXCEEDED") from exc
    except ValueError as exc:
        raise _invalid(str(exc), "INSUFFICIENT_DATA") from exc


def walk_forward(body: WalkForwardRequest, db: Session, user_id: Any, rs: RiskSettings,
                 provider: MarketDataProvider, settings: Settings) -> dict[str, Any]:
    t0 = time.monotonic()
    st, prep, info = _context(body.run, db, user_id, rs, provider, settings)
    if body.grid.thresholds and V.threshold_index(st.strategy) is None:
        raise _invalid("This strategy has no numeric threshold to vary.")
    key = cache.request_hash(["wf", body.model_dump(mode="json"), st.limits, info["fingerprint"]])
    hit = cache.RESULTS.get(key)
    if hit is not None:
        return {**hit, "cached": True}
    budget = V.Budget(TIME_BUDGET_SECONDS)
    with prep.lock:
        res = _validation_errors(lambda: V.walk_forward(
            prep, st.strategy, _grid(body.grid), st.exe, st.risk, seed=body.run.seed,
            train_bars=body.train_bars, test_bars=body.test_bars, holdout_pct=body.holdout_pct,
            objective=body.objective, min_trades=body.min_trades, max_windows=body.max_windows,
            budget=budget))
    oos = res.pop("oos_raw")
    out = {**_header(st, info, key, body.run.seed), **res,
           "oos_by_regime": M.group_summary(oos["trades"], lambda t: t["regime"]),
           "oos_equity_curve": _curve(oos["equity"]),
           "protocol": "Rolling windows: each train window picks the grid point with the best "
                       f"{body.objective} (at least {body.min_trades} trades); it is applied "
                       "unchanged to the next test window. oos_metrics aggregate test windows "
                       "only. The holdout is never used for selection.",
           "computed_ms": round((time.monotonic() - t0) * 1000)}
    cache.RESULTS.put(key, out)
    return out


def sensitivity(body: SensitivityRequest, db: Session, user_id: Any, rs: RiskSettings,
                provider: MarketDataProvider, settings: Settings) -> dict[str, Any]:
    t0 = time.monotonic()
    st, prep, info = _context(body.run, db, user_id, rs, provider, settings)
    if body.grid.thresholds and V.threshold_index(st.strategy) is None:
        raise _invalid("This strategy has no numeric threshold to vary.")
    key = cache.request_hash(["sens", body.model_dump(mode="json"), st.limits,
                              info["fingerprint"]])
    hit = cache.RESULTS.get(key)
    if hit is not None:
        return {**hit, "cached": True}
    budget = V.Budget(TIME_BUDGET_SECONDS)
    with prep.lock:
        res = _validation_errors(lambda: V.sensitivity(
            prep, st.strategy, _grid(body.grid), st.exe, st.risk, seed=body.run.seed,
            budget=budget))
    out = {**_header(st, info, key, body.run.seed), **res,
           "computed_ms": round((time.monotonic() - t0) * 1000)}
    cache.RESULTS.put(key, out)
    return out


def monte_carlo(body: MonteCarloRequest, db: Session, user_id: Any, rs: RiskSettings,
                provider: MarketDataProvider, settings: Settings) -> dict[str, Any]:
    report, nets = run(body.run, db, user_id, rs, provider, settings)
    mc = _validation_errors(lambda: V.monte_carlo(nets, simulations=body.simulations,
                                                  seed=body.seed))
    keep = ("fidelity", "fidelity_note", "data_source", "evidence", "warnings", "request_hash",
            "strategy", "underlying", "timeframe", "data", "seed")
    return {**{k: report[k] for k in keep}, "base_metrics": _brief(report["metrics"]),
            "monte_carlo": mc}
