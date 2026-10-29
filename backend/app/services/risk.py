from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utcnow
from app.models.bots import Bot, RiskSettings
from app.schemas.risk import RiskLimitsOut, RiskLimitsUpdate, RiskStatus, RiskUsage

ENGINE_NOTE = (
    "Paper execution is active. Every entry from every bot is checked against these shared "
    "limits before a simulated order is sent."
)
DISCLAIMER = (
    "Risk limits cannot guarantee a maximum loss during price gaps, illiquidity, broker "
    "outages or execution failures."
)


def get_or_create(db: Session, user_id: uuid.UUID) -> RiskSettings:
    rs = db.get(RiskSettings, user_id)
    if rs is None:
        rs = RiskSettings(user_id=user_id)
        db.add(rs)
        db.flush()
    return rs


def apply_update(rs: RiskSettings, body: RiskLimitsUpdate) -> dict[str, object]:
    changes = body.model_dump(exclude_none=True)
    merged = {c: getattr(rs, c) for c in RiskLimitsUpdate.model_fields} | changes
    capital = Decimal(merged["capital"])
    problems = []
    if Decimal(merged["max_risk_per_trade"]) > capital:
        problems.append("Risk per trade cannot exceed capital.")
    if Decimal(merged["daily_loss_limit"]) > capital:
        problems.append("Daily loss limit cannot exceed capital.")
    if Decimal(merged["max_risk_per_trade"]) > Decimal(merged["daily_loss_limit"]):
        problems.append("Risk per trade cannot exceed the daily loss limit.")
    if problems:
        raise AppError("Risk limits are inconsistent.", code="VALIDATION_ERROR", status_code=422,
                       details=problems)
    for k, v in changes.items():
        setattr(rs, k, v)
    return {k: str(v) for k, v in changes.items()}


def set_kill_switch(rs: RiskSettings, engaged: bool) -> None:
    rs.kill_switch_engaged = engaged
    rs.kill_switch_changed_at = utcnow()


def status(rs: RiskSettings, db: Session | None = None, settings: Settings | None = None,
           ) -> RiskStatus:
    from app.execution import risk as RISK

    zero = Decimal("0")
    if db is None:
        usage = RiskUsage(realized_pnl=zero, unrealized_pnl=zero, estimated_charges=zero,
                          entries_used=0, open_positions=0, planned_risk=zero,
                          daily_loss_used_pct=0.0, profit_lock_reached=False)
        return RiskStatus(mode="PAPER", engine_active=False, engine_note=ENGINE_NOTE,
                          limits=RiskLimitsOut.model_validate(rs), usage=usage,
                          new_entries_allowed=False, block_reasons=[], disclaimer=DISCLAIMER,
                          exposure=[])
    now = utcnow()
    ds = RISK.daily_state(db, rs.user_id, RISK.trading_day(now))
    pos = RISK.open_positions(db, rs.user_id)
    pend = RISK.pending_entries(db, rs.user_id)
    unreal = RISK.unrealized(pos)
    planned = sum(((p.avg_entry - (p.stop_price or p.avg_entry)) * p.quantity for p in pos),
                  zero)
    day_net = ds.realized_net + unreal
    used_pct = float(max(-day_net, zero) / rs.daily_loss_limit * 100) if rs.daily_loss_limit \
        else 0.0
    reasons = []
    if settings is not None and not settings.paper_trading_enabled:
        reasons.append("Paper trading is disabled.")
    if rs.kill_switch_engaged:
        reasons.append("Kill switch is engaged.")
    if ds.loss_limit_hit_at or day_net <= -rs.daily_loss_limit:
        reasons.append("Daily loss stop reached.")
    if ds.profit_lock_hit_at or ds.realized_net >= rs.daily_profit_lock:
        reasons.append("Daily profit lock reached: no new entries today (open positions keep "
                       "their stop-loss and target).")
    if ds.entries + len(pend) >= rs.max_entries_per_day:
        reasons.append(f"All {rs.max_entries_per_day} entries for today are used.")
    if len(pos) + len(pend) >= rs.max_open_positions:
        reasons.append("Maximum open positions reached.")
    if RISK.unresolved_orders(db, rs.user_id):
        reasons.append("An order needs reconciliation.")
    by_bot: dict[str, dict[str, Any]] = {}

    def slot(bot_id: Any) -> dict[str, Any]:
        return by_bot.setdefault(str(bot_id) if bot_id else "manual", {
            "bot_id": str(bot_id) if bot_id else None, "bot_name": None, "positions": 0,
            "pending": 0, "premium": zero, "planned_risk": zero, "underlyings": set()})

    for p in pos:
        e = slot(p.bot_id)
        e["positions"] += 1
        e["premium"] += p.avg_entry * p.quantity
        e["planned_risk"] += (p.avg_entry - (p.stop_price or p.avg_entry)) * p.quantity
        e["underlyings"].add(p.underlying)
    for o in pend:
        e = slot(o.bot_id)
        e["pending"] += 1
        e["underlyings"].add(o.underlying)
    ids = [uuid.UUID(e["bot_id"]) for e in by_bot.values() if e["bot_id"]]
    names = dict(db.execute(select(Bot.id, Bot.name).where(Bot.id.in_(ids))).all()) if ids \
        else {}
    exposure = [{**v, "bot_name": names.get(uuid.UUID(v["bot_id"])) if v["bot_id"] else None,
                 "underlyings": sorted(v["underlyings"]), "premium": str(v["premium"]),
                 "planned_risk": str(v["planned_risk"])} for v in by_bot.values()]
    correlated = {u for p in pos for u in [p.underlying]}
    if len(pos) > 1 and len(correlated) < len(pos):
        reasons.append("Several open positions share the same underlying (correlated).")
    return RiskStatus(
        mode="PAPER", engine_active=bool(settings is None or settings.paper_trading_enabled),
        engine_note=ENGINE_NOTE, limits=RiskLimitsOut.model_validate(rs),
        usage=RiskUsage(realized_pnl=ds.realized_net, unrealized_pnl=unreal,
                        estimated_charges=ds.charges, entries_used=ds.entries + len(pend),
                        open_positions=len(pos), planned_risk=planned,
                        daily_loss_used_pct=round(min(used_pct, 100.0), 1),
                        profit_lock_reached=bool(ds.profit_lock_hit_at)
                        or ds.realized_net >= rs.daily_profit_lock),
        new_entries_allowed=not reasons, block_reasons=reasons, disclaimer=DISCLAIMER,
        exposure=exposure)
