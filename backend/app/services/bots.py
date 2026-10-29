"""Bot configuration management and pre-activation validation."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models.bots import Bot, BotConfigVersion, BotCreationMethod, RiskSettings
from app.schemas.bots import (
    AiDraftRequest,
    AiDraftResponse,
    BotConfig,
    BotDetail,
    BotOut,
    ValidationCheck,
    ValidationReport,
)
from app.strategies.catalog import STRATEGIES

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


def to_out(bot: Bot) -> BotOut:
    return BotOut(
        id=bot.id, name=bot.name, strategy_id=bot.strategy_id,
        strategy_name=STRATEGIES[bot.strategy_id]["name"], underlying=bot.underlying,
        timeframe=bot.timeframe, creation_method=bot.creation_method, status=bot.status,
        mode=bot.mode, current_version=bot.current_version, created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


def current_config(bot: Bot) -> BotConfig:
    v = next(v for v in bot.versions if v.version == bot.current_version)
    return BotConfig.model_validate(v.config)


def to_detail(bot: Bot) -> BotDetail:
    return BotDetail(**to_out(bot).model_dump(), config=current_config(bot),
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


def validate(bot: Bot, cfg: BotConfig, rs: RiskSettings) -> ValidationReport:
    checks: list[ValidationCheck] = []

    def add(key: str, label: str, ok: bool | None, message: str) -> None:
        status = "PASS" if ok else "WARN" if ok is None else "FAIL"
        checks.append(ValidationCheck(key=key, label=label, status=status, message=message))

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
    add("contract", "Contract, lot and tick size", False,
        "No authorised instrument master yet (Phase 3), so the lot size, tick size and "
        "expiry cannot be verified.")
    add("data", "Market data", False,
        "Only synthetic DEMO data is available. Trading decisions need a live, authorised feed.")
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
        "Paper mode only; no broker is required. Live trading is locked.")
    add("engine", "Execution engine", False,
        "The paper execution engine and global risk manager arrive in Phase 6.")

    return ValidationReport(
        bot_id=bot.id, version=bot.current_version,
        can_activate=all(c.status != "FAIL" for c in checks), checks=checks,
    )


_STYLE_TO_STRATEGY = {
    "MOMENTUM": "momentum", "SCALPING": "scalping", "BREAKOUT": "breakout",
    "REVERSAL": "reversal", "SMC": "smc", "AUTO": "momentum",
}
_PROFILE = {
    # (fraction of global risk per trade, stop %, target %, entries)
    "CONSERVATIVE": (Decimal("0.6"), Decimal("20"), Decimal("30"), 1),
    "MODERATE": (Decimal("0.8"), Decimal("25"), Decimal("40"), 2),
    "AGGRESSIVE": (Decimal("1.0"), Decimal("30"), Decimal("50"), 2),
}


def ai_draft(req: AiDraftRequest, rs: RiskSettings) -> AiDraftResponse:
    """Rule-based draft generator. No AI model is involved yet."""
    strategy_id = _STYLE_TO_STRATEGY[req.style]
    strat = STRATEGIES[strategy_id]
    frac, sl, tgt, entries = _PROFILE[req.risk_profile]
    capital = min(req.capital, rs.capital)
    risk_per_trade = min(rs.max_risk_per_trade * frac, capital).quantize(Decimal("1"))
    timeframe = req.timeframe if req.timeframe in strat["timeframes"] else strat["timeframes"][0]
    cfg = BotConfig.model_validate({
        "name": f"{req.underlying} {strat['name']} {req.option_type}",
        "description": f"Draft from AI Auto Create ({req.risk_profile.lower()} profile).",
        "strategy_id": strategy_id,
        "timeframe": timeframe,
        "instrument": {"underlying": req.underlying, "option_type": req.option_type},
        "entry": {"logic": "ALL", "conditions": strat["default_entry"]},
        "exit": {"stop_loss": {"type": "PERCENT", "value": sl},
                 "target": {"type": "PERCENT", "value": tgt},
                 "square_off_time_ist": rs.square_off_time_ist},
        "risk": {"capital_allocation": capital, "risk_per_trade": risk_per_trade,
                 "max_entries_per_day": min(entries, rs.max_entries_per_day),
                 "max_open_positions": 1},
    })
    rationale = [
        f"Strategy: {strat['name']} (chosen from your '{req.style.lower()}' style).",
        f"Timeframe {timeframe}.",
        f"Risk per trade {inr(risk_per_trade)}: {int(frac * 100)}% of your global maximum "
        f"{inr(rs.max_risk_per_trade)}.",
        f"Option premium stop-loss {sl}% and target {tgt}%.",
    ]
    warnings = [
        "Generated by rules, not an AI model. Market analysis is not used until Phase 4.",
        "Review every field. The independent risk manager will still validate it.",
    ]
    if req.style == "AUTO":
        warnings.append("AUTO has no regime detector yet, so it defaults to Momentum.")
    if req.capital > rs.capital:
        warnings.append(f"Capital was capped at your global capital of {inr(rs.capital)}.")
    return AiDraftResponse(generator="rule-based-v1", config=cfg, rationale=rationale,
                           warnings=warnings)
