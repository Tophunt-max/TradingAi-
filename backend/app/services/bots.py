"""Bot configuration management and pre-activation validation."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bots.lifecycle import latest_run
from app.core.config import MarketDataMode, Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.time import IST, utcnow
from app.market_data.factory import build_provider
from app.models.bots import (
    Bot,
    BotConfigVersion,
    BotCreationMethod,
    BotRun,
    BotSignal,
    BotStatus,
    RiskSettings,
)
from app.models.instruments import Instrument
from app.schemas.bots import (
    AiDraftRequest,
    AiDraftResponse,
    BotConfig,
    BotDetail,
    BotOut,
    BotRunOut,
    ValidationCheck,
    ValidationReport,
)
from app.strategies.catalog import STRATEGIES
from app.strategies.conditions import mirror
from app.strategies.conditions import supported as cond_supported

MAX_BOTS_PER_USER = 50


def inr(value: Decimal) -> str:
    """Indian-style rupee display for messages, e.g. ₹10,000 or ₹250.50."""
    q = Decimal(value).quantize(Decimal("0.01"))
    whole, frac = divmod(abs(q), 1)
    digits = str(int(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        head = ",".join(re.findall(r"\d{1,2}(?=(?:\d{2})*$)", head))
        digits = f"{head},{tail}"
    sign = "-" if q < 0 else ""
    return f"{sign}₹{digits}" + (f".{int(frac * 100):02d}" if frac else "")


WORKER_STALE_SECONDS = 120


def _health(bot: Bot, run: BotRun | None, kill_switch: bool) -> tuple[str, str | None]:
    if bot.status == BotStatus.PAUSED:
        return "PAUSED", bot.status_reason
    if bot.status != BotStatus.ACTIVE:
        return "NOT_RUNNING", None
    if run is None:
        return "ERROR", "Active without a run record."
    if kill_switch:
        return "BLOCKED", "Kill switch engaged: signals are suppressed."
    if run.consecutive_errors:
        return "ERROR", run.last_error
    if run.last_heartbeat_at is None:
        return "WAITING_FOR_WORKER", "No worker has evaluated this bot yet."
    age = (utcnow() - run.last_heartbeat_at).total_seconds()
    if age > WORKER_STALE_SECONDS:
        return "STALE", f"Last evaluated {int(age)} s ago: is the bot worker running?"
    last = f"{run.last_bar_ts.astimezone(IST):%d %b %H:%M} IST" if run.last_bar_ts else "—"
    return "OK", f"Evaluating each closed candle; last candle {last}."


def _today_pnl(db: Session, bot_id: uuid.UUID) -> Decimal | None:
    """Paper net P&L for today (IST): realised net of closed trades plus open mark-to-market.
    None when the bot has no trades today, so the UI shows a dash instead of a fake zero."""
    from app.models.execution import Position

    day0 = datetime.combine(utcnow().astimezone(IST).date(), time(0), IST)
    rows = db.scalars(select(Position).where(
        Position.bot_id == bot_id,
        (Position.status != "CLOSED") | (Position.closed_at >= day0))).all()
    if not rows:
        return None
    total = Decimal("0")
    for p in rows:
        total += p.realized_net
        if p.status != "CLOSED" and p.last_price is not None:
            total += (p.last_price - p.avg_entry) * p.quantity
    return total.quantize(Decimal("0.01"))


def to_out(bot: Bot, db: Session | None = None) -> BotOut:
    out = BotOut(
        id=bot.id, name=bot.name, strategy_id=bot.strategy_id,
        strategy_name=STRATEGIES[bot.strategy_id]["name"], underlying=bot.underlying,
        timeframe=bot.timeframe, creation_method=bot.creation_method, status=bot.status,
        mode=bot.mode, current_version=bot.current_version, created_at=bot.created_at,
        updated_at=bot.updated_at, status_reason=bot.status_reason,
        status_changed_at=bot.status_changed_at,
    )
    if db is None:
        return out
    run = latest_run(db, bot.id)
    rs = db.get(RiskSettings, bot.user_id)
    out.health, out.health_detail = _health(bot, run if run and run.status != "ENDED" else None,
                                            bool(rs and rs.kill_switch_engaged))
    out.run = BotRunOut.model_validate(run) if run else None
    out.today_pnl = _today_pnl(db, bot.id)
    from app.models.execution import Position

    out.trade_count = db.scalar(select(func.count()).select_from(Position)
                                .where(Position.bot_id == bot.id)) or 0
    last = db.scalar(select(BotSignal).where(BotSignal.bot_id == bot.id)
                     .order_by(BotSignal.bar_ts.desc()).limit(1))
    if last:
        out.last_signal = f"{last.side} at {last.price:.2f}"
        out.last_signal_at = last.bar_ts
        day0 = datetime.combine(utcnow().astimezone(IST).date(), time(0), IST)
        out.signals_today = db.scalar(select(func.count()).select_from(BotSignal).where(
            BotSignal.bot_id == bot.id, BotSignal.bar_ts >= day0)) or 0
    return out


def current_config(bot: Bot) -> BotConfig:
    v = next(v for v in bot.versions if v.version == bot.current_version)
    return BotConfig.model_validate(v.config)


def to_detail(bot: Bot, db: Session | None = None) -> BotDetail:
    return BotDetail(**to_out(bot, db).model_dump(), config=current_config(bot),
                     versions=[v.version for v in bot.versions])


def get_owned(db: Session, user_id: uuid.UUID, bot_id: uuid.UUID) -> Bot:
    bot = db.get(Bot, bot_id)
    if bot is None or bot.user_id != user_id:
        raise NotFoundError("Bot not found.")
    return bot


def _sync_columns(bot: Bot, cfg: BotConfig) -> None:
    bot.name = cfg.name
    bot.strategy_id = cfg.strategy_id
    bot.underlying = cfg.instrument.underlying
    bot.timeframe = cfg.timeframe


def create(db: Session, user_id: uuid.UUID, cfg: BotConfig, method: BotCreationMethod) -> Bot:
    count = len(db.scalars(select(Bot.id).where(Bot.user_id == user_id)).all())
    if count >= MAX_BOTS_PER_USER:
        raise ConflictError(f"You can have at most {MAX_BOTS_PER_USER} bots.",
                            code="BOT_LIMIT_REACHED")
    bot = Bot(user_id=user_id, creation_method=method, current_version=1)
    _sync_columns(bot, cfg)
    bot.versions.append(BotConfigVersion(version=1, config=cfg.model_dump(mode="json")))
    db.add(bot)
    db.flush()
    return bot


def update(db: Session, bot: Bot, cfg: BotConfig) -> Bot:
    new_version = bot.current_version + 1
    bot.versions.append(BotConfigVersion(version=new_version, config=cfg.model_dump(mode="json")))
    bot.current_version = new_version
    _sync_columns(bot, cfg)
    db.flush()
    return bot


def clone(db: Session, user_id: uuid.UUID, bot: Bot) -> Bot:
    cfg = current_config(bot).model_copy(update={"name": f"{bot.name} (copy)"[:80]})
    return create(db, user_id, cfg, BotCreationMethod.CLONE)


@dataclass
class MarketContext:
    """What the market-data layer knows about the bot's contract right now."""

    mode: str  # DEMO | INDSTOCKS | DISABLED
    provider_ok: bool = False
    provider_detail: str | None = None
    contract: Instrument | None = None


def market_context(db: Session, settings: Settings, cfg: BotConfig) -> MarketContext:
    mode = settings.market_data_mode.value
    if settings.market_data_mode != MarketDataMode.INDSTOCKS:
        return MarketContext(mode=mode)
    today = utcnow().astimezone(IST).date()
    contract = db.scalar(
        select(Instrument)
        .where(
            Instrument.provider == "INDSTOCKS",
            Instrument.source == "fno",
            Instrument.symbol_name == cfg.instrument.underlying,
            Instrument.instrument_name == "OPTIDX",
            Instrument.option_type == cfg.instrument.option_type,
            Instrument.expiry_date >= today,
        )
        .order_by(Instrument.expiry_date)
        .limit(1)
    )
    try:
        st = build_provider(db, settings).status()
        ok, detail = st.get("status") == "ok", st.get("detail")
    except Exception as exc:  # status must never break validation
        ok, detail = False, type(exc).__name__
    return MarketContext(mode=mode, provider_ok=ok, provider_detail=detail, contract=contract)


def validate(
    bot: Bot, cfg: BotConfig, rs: RiskSettings, market: MarketContext | None = None,
    paper_enabled: bool = True,
) -> ValidationReport:
    market = market or MarketContext(mode="DEMO")
    checks: list[ValidationCheck] = []

    def add(key: str, label: str, ok: bool | None, message: str,
            required_for: str = "ACTIVATION") -> None:
        status = "PASS" if ok else "WARN" if ok is None else "FAIL"
        checks.append(ValidationCheck(key=key, label=label, status=status, message=message,
                                      required_for=required_for))

    strat = STRATEGIES[cfg.strategy_id]
    add("strategy", "Strategy compatibility", not strat["research_only"],
        "Research-only strategy; it cannot be activated." if strat["research_only"]
        else f"{strat['name']} v{strat['version']}.")
    add("timeframe", "Timeframe", cfg.timeframe in strat["timeframes"] or None,
        f"{cfg.timeframe} is supported." if cfg.timeframe in strat["timeframes"]
        else f"{cfg.timeframe} is outside this strategy's usual timeframes "
             f"({', '.join(strat['timeframes'])}).")
    add("instrument", "Instrument eligibility", True,
        f"{cfg.instrument.underlying} {cfg.instrument.option_type} options buying.")
    conds = [c.model_dump() for c in cfg.entry.conditions]
    problems = [p for p in (cond_supported(c) for c in conds) if p]
    if not conds:
        add("conditions", "Entry conditions", False, "Add at least one entry condition.")
    else:
        add("conditions", "Entry conditions", not problems,
            "; ".join(problems) if problems else
            f"{len(conds)} condition(s), {'all' if cfg.entry.logic == 'ALL' else 'any'} must "
            "match, evaluated on each closed candle.")
    c = market.contract
    if c is not None:
        add("contract", "Contract, lot and tick size", True,
            f"Nearest expiry {c.expiry_date:%d %b %Y}: lot size {c.lot_size}, tick size "
            f"{c.tick_size.normalize() if c.tick_size is not None else 'unknown'} "
            "(INDstocks instrument master).", "ORDERS")
    elif market.mode == "INDSTOCKS":
        add("contract", "Contract, lot and tick size", False,
            "No current contract found in the instrument master. Run an instrument sync.",
            "ORDERS")
    else:
        add("contract", "Contract, lot and tick size", None,
            "DEMO mode has no instrument master: paper orders use a labelled placeholder "
            "of 1 unit per lot, not exchange metadata.", "ORDERS")
    if market.mode == "INDSTOCKS":
        add("data", "Market data", market.provider_ok,
            "INDstocks data is available." if market.provider_ok
            else f"INDstocks is not usable: {market.provider_detail}.", "ORDERS")
    else:
        add("data", "Market data", None,
            "Only synthetic DEMO data is available. Paper trades on DEMO data are for "
            "testing the app only.", "ORDERS")
    add("capital", "Capital allocation", cfg.risk.capital_allocation <= rs.capital,
        f"{inr(cfg.risk.capital_allocation)} of {inr(rs.capital)} global capital."
        if cfg.risk.capital_allocation <= rs.capital
        else f"Allocation exceeds your global capital of {inr(rs.capital)}.")
    add("risk_per_trade", "Risk per trade", cfg.risk.risk_per_trade <= rs.max_risk_per_trade,
        f"{inr(cfg.risk.risk_per_trade)} (global maximum {inr(rs.max_risk_per_trade)}).")
    add("entries", "Entries per day", cfg.risk.max_entries_per_day <= rs.max_entries_per_day,
        f"{cfg.risk.max_entries_per_day} (global maximum {rs.max_entries_per_day}, "
        "shared by all bots).")
    add("positions", "Open positions", cfg.risk.max_open_positions <= rs.max_open_positions,
        f"{cfg.risk.max_open_positions} (global maximum {rs.max_open_positions}, "
        "shared by all bots).")
    add("re_entry", "Re-entry after stop-loss",
        not cfg.risk.re_entry_after_stop_loss or rs.re_entry_after_stop_loss,
        "Allowed." if cfg.risk.re_entry_after_stop_loss and rs.re_entry_after_stop_loss
        else "Disabled." if not cfg.risk.re_entry_after_stop_loss
        else "Your global risk settings disable re-entry after a stop-loss.")
    add("square_off", "Session square-off", cfg.exit.square_off_time_ist <= rs.square_off_time_ist,
        f"{cfg.exit.square_off_time_ist} IST (global {rs.square_off_time_ist} IST).")
    sl_ok = cfg.exit.stop_loss.value > 0
    add("stop_loss", "Stop-loss", sl_ok,
        f"{cfg.exit.stop_loss.value} {cfg.exit.stop_loss.type.lower()}.")
    if cfg.exit.stop_loss.type == cfg.exit.target.type:
        rr = cfg.exit.target.value / cfg.exit.stop_loss.value
        add("reward_risk", "Target vs stop", rr >= Decimal("1") or None,
            f"Target / stop ratio {rr:.2f}. This is not a probability of profit.")
    add("kill_switch", "Kill switch", not rs.kill_switch_engaged,
        "Engaged: new entries are blocked." if rs.kill_switch_engaged else "Not engaged.")
    add("broker", "Broker permissions", None,
        "Paper mode only; no broker is required. Live trading is locked.", "ORDERS")
    add("engine", "Execution engine", paper_enabled,
        "Paper execution with the global risk manager: every entry is re-checked against your "
        "shared limits before a simulated order is sent." if paper_enabled else
        "Paper trading is disabled, so the bot runs signal-only.", "ORDERS")
    add("live", "Live trading", None,
        "Locked: no verified broker order adapter (Phase 7).", "ORDERS")

    activation = [c for c in checks if c.required_for == "ACTIVATION"]
    return ValidationReport(
        bot_id=bot.id, version=bot.current_version,
        can_activate=all(c.status != "FAIL" for c in activation),
        activation_mode="PAPER" if paper_enabled else "SIGNAL_ONLY",
        can_place_orders=all(c.status != "FAIL" for c in checks),
        checks=checks,
    )


_STYLE_TO_STRATEGY = {
    "MOMENTUM": "momentum", "SCALPING": "scalping", "BREAKOUT": "breakout",
    "REVERSAL": "reversal", "SMC": "smc",
}
# Regime family -> strategy designed for it (used by AUTO style).
_REGIME_TO_STRATEGY = {"TRENDING": "momentum", "RANGE_EXPANSION": "breakout", "RANGE": "reversal",
                       "VOLATILE": "smc"}
_PROFILE = {
    # (fraction of global risk per trade, stop %, target %, entries)
    "CONSERVATIVE": (Decimal("0.6"), Decimal("20"), Decimal("30"), 1),
    "MODERATE": (Decimal("0.8"), Decimal("25"), Decimal("40"), 2),
    "AGGRESSIVE": (Decimal("1.0"), Decimal("30"), Decimal("50"), 2),
}


def ai_draft(req: AiDraftRequest, rs: RiskSettings,
             analysis: dict[str, Any] | None = None) -> AiDraftResponse:
    """Draft from the analysis engine (regime + trend) and the user's risk limits.

    Rules decide everything; no ML model chooses the configuration.
    """
    rationale: list[str] = []
    warnings: list[str] = []
    regime = (analysis or {}).get("regime")
    trend = (analysis or {}).get("trend")
    if analysis:
        rationale.append(
            f"{req.underlying} {analysis['timeframe']}: "
            f"{regime['label'].replace('_', ' ').lower() if regime else 'unknown'} regime, "
            f"{(trend or 'unknown').lower()} trend reading ({analysis['source']} data).")
        if analysis["source"] == "DEMO":
            warnings.append("Market analysis used synthetic DEMO data.")
    else:
        warnings.append("Market analysis was unavailable, so style and side defaults were used.")

    if req.style == "AUTO":
        fam = regime["family"] if regime else None
        strategy_id = _REGIME_TO_STRATEGY.get(fam or "", "momentum")
        rationale.append(f"Strategy: {STRATEGIES[strategy_id]['name']}, designed for the "
                         f"{(fam or 'unknown').lower()} regime.")
        if fam == "VOLATILE":
            warnings.append("Volatility is unusually high; consider waiting for calmer conditions.")
    else:
        strategy_id = _STYLE_TO_STRATEGY[req.style]
        rationale.append(f"Strategy: {STRATEGIES[strategy_id]['name']} (your chosen style).")
        if regime and not ("ANY" in STRATEGIES[strategy_id]["regimes"]
                           or regime["family"] in STRATEGIES[strategy_id]["regimes"]):
            warnings.append(f"The current {regime['label'].replace('_', ' ').lower()} regime is "
                            f"outside this strategy's design.")
    strat = STRATEGIES[strategy_id]

    if req.option_type == "AUTO":
        label = regime["label"] if regime else None
        side = ("CE" if label == "TRENDING_UP" else "PE" if label == "TRENDING_DOWN"
                else "PE" if trend == "BEARISH" else "CE")
        basis = "regime" if label in ("TRENDING_UP", "TRENDING_DOWN") else "trend reading"
        rationale.append(f"Side: {side}, from the {basis}.")
        if trend in (None, "SIDEWAYS") and label not in ("TRENDING_UP", "TRENDING_DOWN"):
            warnings.append("No clear direction: the side defaulted to CE. Review it.")
    else:
        side = req.option_type

    frac, sl, tgt, entries = _PROFILE[req.risk_profile]
    capital = min(req.capital, rs.capital)
    risk_per_trade = min(rs.max_risk_per_trade * frac, capital).quantize(Decimal("1"))
    timeframe = req.timeframe if req.timeframe in strat["timeframes"] else strat["timeframes"][0]
    if timeframe != req.timeframe:
        warnings.append(f"Timeframe changed to {timeframe}: {strat['name']} is designed for "
                        f"{', '.join(strat['timeframes'])}.")
    conditions = strat["default_entry"] if side == "CE" else [
        mirror(c) for c in strat["default_entry"]]
    cfg = BotConfig.model_validate({
        "name": f"{req.underlying} {strat['name']} {side}",
        "description": f"Draft from AI Auto Create ({req.risk_profile.lower()} profile).",
        "strategy_id": strategy_id,
        "timeframe": timeframe,
        "instrument": {"underlying": req.underlying, "option_type": side},
        "entry": {"logic": "ALL", "conditions": conditions},
        "exit": {"stop_loss": {"type": "PERCENT", "value": sl},
                 "target": {"type": "PERCENT", "value": tgt},
                 "square_off_time_ist": rs.square_off_time_ist},
        "risk": {"capital_allocation": capital, "risk_per_trade": risk_per_trade,
                 "max_entries_per_day": min(entries, rs.max_entries_per_day),
                 "max_open_positions": 1},
    })
    rationale += [
        f"Timeframe {timeframe}.",
        f"Risk per trade {inr(risk_per_trade)}: {int(frac * 100)}% of your global maximum "
        f"{inr(rs.max_risk_per_trade)}.",
        f"Option premium stop-loss {sl}% and target {tgt}%.",
    ]
    warnings += [
        "Chosen by rules from the analysis engine, not by a machine-learning model.",
        "Review every field. The independent risk manager will still validate it.",
    ]
    if req.capital > rs.capital:
        warnings.append(f"Capital was capped at your global capital of {inr(rs.capital)}.")
    return AiDraftResponse(generator="analysis-rules-v2", config=cfg, rationale=rationale,
                           warnings=warnings, analysis_regime=regime["label"] if regime else None,
                           analysis_trend=trend)
