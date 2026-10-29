"""Global, deterministic risk manager.

Independent of strategies and AI: every entry order (bot or manual) passes
`pretrade` while holding a per-user lock, so concurrent bots cannot jointly
exceed the shared limits. The daily state is persisted, so limits survive
application and worker restarts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import MarketDataMode, Settings
from app.core.time import IST
from app.market_data.session import session_state
from app.models.bots import RiskSettings
from app.models.execution import ACTIVE_ORDER_STATES, DailyRiskState, Order, Position

D = Decimal
ZERO = D("0")


def trading_day(now: datetime) -> date:
    return now.astimezone(IST).date()


def lock_user(db: Session, user_id: uuid.UUID) -> RiskSettings:
    """Serialise risk decisions per user (row lock on PostgreSQL)."""
    rs = db.scalar(select(RiskSettings).where(RiskSettings.user_id == user_id)
                   .with_for_update())
    if rs is None:
        rs = RiskSettings(user_id=user_id)
        db.add(rs)
        db.flush()
    return rs


def daily_state(db: Session, user_id: uuid.UUID, day: date) -> DailyRiskState:
    ds = db.scalar(select(DailyRiskState).where(DailyRiskState.user_id == user_id,
                                                DailyRiskState.trading_day == day))
    if ds is None:
        ds = DailyRiskState(user_id=user_id, trading_day=day)
        db.add(ds)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            ds = db.scalar(select(DailyRiskState).where(DailyRiskState.user_id == user_id,
                                                        DailyRiskState.trading_day == day))
    return ds  # type: ignore[return-value]


def open_positions(db: Session, user_id: uuid.UUID) -> list[Position]:
    return list(db.scalars(select(Position).where(Position.user_id == user_id,
                                                  Position.status != "CLOSED")).all())


def pending_entries(db: Session, user_id: uuid.UUID) -> list[Order]:
    return list(db.scalars(select(Order).where(
        Order.user_id == user_id, Order.purpose == "ENTRY", Order.filled_qty == 0,
        Order.status.in_(ACTIVE_ORDER_STATES))).all())


def unresolved_orders(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Order).where(
        Order.user_id == user_id,
        Order.status.in_(("UNKNOWN", "RECONCILIATION_REQUIRED")))) or 0


def unrealized(positions: list[Position]) -> Decimal:
    total = ZERO
    for p in positions:
        if p.last_price is not None:
            total += (p.last_price - p.avg_entry) * p.quantity
    return total


def account(db: Session, rs: RiskSettings, user_id: uuid.UUID) -> dict[str, Decimal]:
    """Paper account: capital + all realised net P&L - money tied up in positions/orders."""
    closed_net = db.scalar(select(func.coalesce(
        func.sum(Position.realized_gross - Position.charges_total), 0)).where(
        Position.user_id == user_id)) or ZERO
    pos = open_positions(db, user_id)
    used = sum((p.avg_entry * p.quantity for p in pos), ZERO)
    reserved = ZERO
    for o in pending_entries(db, user_id):
        ref = o.limit_price or D(str(o.risk_snapshot.get("entry_price", "0")))
        reserved += ref * o.quantity
    equity_cash = rs.capital + D(closed_net)
    return {"capital": rs.capital, "realized_net_total": D(closed_net), "used_margin": used,
            "reserved": reserved, "available": equity_cash - used - reserved,
            "unrealized": unrealized(pos)}


@dataclass
class Decision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)


def _hhmm(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def pretrade(db: Session, settings: Settings, rs: RiskSettings, *, user_id: uuid.UUID,
             now: datetime, lots: int, lot_size: int, entry_price: Decimal,
             stop_price: Decimal, round_trip_charges: Decimal, buy_charges: Decimal,
             quote_ok: bool, quote_detail: str, expiry: date,
             bot_id: uuid.UUID | None = None, bot_risk_per_trade: Decimal | None = None,
             bot_square_off: str | None = None) -> Decision:
    ds = daily_state(db, user_id, trading_day(now))
    pos = open_positions(db, user_id)
    pend = pending_entries(db, user_id)
    acct = account(db, rs, user_id)
    qty = lots * lot_size
    planned = (entry_price - stop_price) * qty + round_trip_charges
    cost = entry_price * qty + buy_charges
    day_net = ds.realized_net + acct["unrealized"]
    local = now.astimezone(IST)
    square_off = min(_hhmm(rs.square_off_time_ist), _hhmm(bot_square_off or "23:59"))
    budget = min(rs.max_risk_per_trade, bot_risk_per_trade or rs.max_risk_per_trade)
    entries_used = ds.entries + len(pend)
    stopped_today = False
    if bot_id is not None and not rs.re_entry_after_stop_loss:
        stopped_today = db.scalar(select(func.count()).select_from(Position).where(
            Position.bot_id == bot_id, Position.exit_reason == "STOP_LOSS",
            Position.closed_at >= datetime.combine(local.date(), time(0), IST))) > 0
    demo_open = (settings.market_data_mode == MarketDataMode.DEMO
                 and settings.demo_market_always_open)
    market_ok = True
    if settings.market_data_mode == MarketDataMode.INDSTOCKS:
        market_ok = session_state(now, settings.market_holidays).is_open

    checks = [
        ("paper", "Paper trading enabled", settings.paper_trading_enabled,
         "Paper trading is disabled."),
        ("kill_switch", "Kill switch off", not rs.kill_switch_engaged,
         "Kill switch is engaged."),
        ("reconcile", "No unresolved orders", unresolved_orders(db, user_id) == 0,
         "An order's outcome is unknown; resolve it before new entries."),
        ("loss_limit", "Daily loss stop", ds.loss_limit_hit_at is None
         and day_net > -rs.daily_loss_limit,
         f"Daily loss stop reached (net {day_net:.2f} vs -{rs.daily_loss_limit})."),
        ("profit_lock", "Daily profit lock", ds.profit_lock_hit_at is None
         and ds.realized_net < rs.daily_profit_lock,
         f"Daily profit lock reached (realised net {ds.realized_net:.2f})."),
        ("entries", "Entries per day (all bots)", entries_used < rs.max_entries_per_day,
         f"{entries_used} of {rs.max_entries_per_day} entries already used today."),
        ("positions", "Open positions (all bots)", len(pos) + len(pend) < rs.max_open_positions,
         f"{len(pos)} open position(s) and {len(pend)} pending entr(ies); "
         f"maximum {rs.max_open_positions}."),
        ("lots", "Lots per trade", 1 <= lots <= rs.max_lots_per_trade,
         f"{lots} lot(s); allowed 1–{rs.max_lots_per_trade}."),
        ("risk", "Planned risk per trade", planned <= budget,
         f"Planned risk {planned:.2f} (stop distance + charges) exceeds {budget:.2f}."),
        ("funds", "Available funds", cost <= acct["available"],
         f"Needs {cost:.2f}; available {acct['available']:.2f}."),
        ("re_entry", "Re-entry after stop-loss", not stopped_today,
         "This bot hit a stop-loss today and re-entry is disabled."),
        ("session", "Before square-off time", demo_open or local.time() < square_off,
         f"No new entries after {square_off:%H:%M} IST."),
        ("market", "Market open", market_ok, "The exchange session is closed."),
        ("expiry", "Contract not expired", expiry > local.date()
         or (expiry == local.date() and local.time() < square_off),
         "The contract expires before the square-off time."),
        ("data", "Fresh, valid quote", quote_ok, quote_detail),
    ]
    out = Decision(allowed=True)
    for key, label, ok, why in checks:
        out.checks.append({"key": key, "label": label, "passed": bool(ok),
                           "detail": None if ok else why})
        if not ok:
            out.allowed = False
            out.reasons.append(why)
    out.snapshot = {
        "entry_price": str(entry_price), "stop_price": str(stop_price), "qty": qty,
        "planned_risk": str(planned.quantize(D("0.01"))), "cost": str(cost.quantize(D("0.01"))),
        "available": str(acct["available"].quantize(D("0.01"))),
        "day_net": str(day_net.quantize(D("0.01"))), "entries_used": entries_used,
        "open_positions": len(pos), "pending_entries": len(pend),
    }
    return out
