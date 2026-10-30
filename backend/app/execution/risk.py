"""Global, deterministic risk manager.

Independent of strategies and AI: every entry order (bot or manual) passes
`pretrade` while holding a per-user lock, so concurrent bots cannot jointly
exceed the shared limits. The daily state is persisted, so limits survive
application and worker restarts.

Limits (RiskSettings) are one policy per user, applied identically in both account
modes. Usage (positions, pending entries, daily counters, funds) is evaluated per
account, so the limits are global across all bots *within* an account mode and DEMO
activity never consumes REAL limits (or the reverse).
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

from app.accounts import core as ACC
from app.core.config import MarketDataMode, Settings
from app.core.time import IST
from app.market_data.session import session_state
from app.models.accounts import Account
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


def daily_state(db: Session, account: Account, day: date) -> DailyRiskState:
    ds = db.scalar(select(DailyRiskState).where(DailyRiskState.account_id == account.id,
                                                DailyRiskState.trading_day == day))
    if ds is None:
        ds = DailyRiskState(user_id=account.user_id, account_id=account.id,
                            account_mode=account.mode, trading_day=day, realized_gross=ZERO,
                            charges=ZERO, entries=0, stop_loss_exits=0)
        db.add(ds)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            ds = db.scalar(select(DailyRiskState).where(
                DailyRiskState.account_id == account.id, DailyRiskState.trading_day == day))
    return ds  # type: ignore[return-value]


def peek_daily_state(db: Session, account: Account | None, day: date) -> DailyRiskState:
    """Read-only view: the stored row, or an unsaved all-zero row (nothing traded yet)."""
    ds = db.scalar(select(DailyRiskState).where(
        DailyRiskState.account_id == account.id, DailyRiskState.trading_day == day)) \
        if account is not None else None
    return ds or DailyRiskState(trading_day=day, realized_gross=ZERO, charges=ZERO, entries=0,
                                stop_loss_exits=0)


def open_positions(db: Session, account_id: uuid.UUID | None) -> list[Position]:
    return list(db.scalars(select(Position).where(Position.account_id == account_id,
                                                  Position.status != "CLOSED")).all())


def pending_entries(db: Session, account_id: uuid.UUID | None) -> list[Order]:
    return list(db.scalars(select(Order).where(
        Order.account_id == account_id, Order.purpose == "ENTRY", Order.filled_qty == 0,
        Order.status.in_(ACTIVE_ORDER_STATES))).all())


def unresolved_orders(db: Session, account_id: uuid.UUID | None) -> int:
    return db.scalar(select(func.count()).select_from(Order).where(
        Order.account_id == account_id,
        Order.status.in_(("UNKNOWN", "RECONCILIATION_REQUIRED")))) or 0


def unrealized(positions: list[Position]) -> Decimal:
    total = ZERO
    for p in positions:
        if p.last_price is not None:
            total += (p.last_price - p.avg_entry) * p.quantity
    return total


def funds(db: Session, account: Account) -> dict[str, Decimal | None]:
    """Funds of one account.

    DEMO: cash is the ledger balance (premium paid and charges are already debited, sale
    proceeds credited); available = cash - premium reserved by pending entry orders;
    equity = cash + market value of open positions.
    REAL: no broker balance source exists, so cash/available/equity are None and the
    pre-trade funds check fails closed.
    """
    closed_net = D(db.scalar(select(func.coalesce(
        func.sum(Position.realized_gross - Position.charges_total), 0)).where(
        Position.account_id == account.id)) or ZERO)
    pos = open_positions(db, account.id)
    used = sum((p.avg_entry * p.quantity for p in pos), ZERO)
    value = sum(((p.last_price if p.last_price is not None else p.avg_entry) * p.quantity
                 for p in pos), ZERO)
    reserved = ZERO
    for o in pending_entries(db, account.id):
        ref = o.limit_price or D(str(o.risk_snapshot.get("entry_price", "0")))
        reserved += ref * o.quantity
    cash = ACC.cash(db, account)
    return {"opening_balance": account.opening_balance, "cash": cash,
            "available": cash - reserved if cash is not None else None,
            "equity": cash + value if cash is not None else None,
            "realized_net_total": closed_net, "used_margin": used, "reserved": reserved,
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


def pretrade(db: Session, settings: Settings, rs: RiskSettings, *, account: Account,
             now: datetime, lots: int, lot_size: int, entry_price: Decimal,
             stop_price: Decimal, round_trip_charges: Decimal, buy_charges: Decimal,
             quote_ok: bool, quote_detail: str, expiry: date,
             bot_id: uuid.UUID | None = None, bot_risk_per_trade: Decimal | None = None,
             bot_square_off: str | None = None,
             account_blockers: list[str] | None = None) -> Decision:
    return _evaluate(db, settings, rs, daily_state(db, account, trading_day(now)),
                     account=account, now=now, lots=lots, lot_size=lot_size,
                     entry_price=entry_price, stop_price=stop_price,
                     round_trip_charges=round_trip_charges, buy_charges=buy_charges,
                     quote_ok=quote_ok, quote_detail=quote_detail, expiry=expiry,
                     bot_id=bot_id, bot_risk_per_trade=bot_risk_per_trade,
                     bot_square_off=bot_square_off, account_blockers=account_blockers)


def pretrade_dry_run(db: Session, settings: Settings, rs: RiskSettings, *, account: Account,
                     now: datetime, lots: int, lot_size: int, entry_price: Decimal,
                     stop_price: Decimal, round_trip_charges: Decimal, buy_charges: Decimal,
                     quote_ok: bool, quote_detail: str, expiry: date,
                     account_blockers: list[str] | None = None) -> Decision:
    """The same checks as `pretrade`, read-only: no lock, no daily-state row is created, no
    order or reservation exists afterwards. Used by the decision engine to tell the user
    whether a proposal would pass now; the real `pretrade` re-checks at order time."""
    return _evaluate(db, settings, rs, peek_daily_state(db, account, trading_day(now)),
                     account=account, now=now, lots=lots, lot_size=lot_size,
                     entry_price=entry_price, stop_price=stop_price,
                     round_trip_charges=round_trip_charges, buy_charges=buy_charges,
                     quote_ok=quote_ok, quote_detail=quote_detail, expiry=expiry,
                     account_blockers=account_blockers)


def _evaluate(db: Session, settings: Settings, rs: RiskSettings, ds: DailyRiskState, *,
              account: Account, now: datetime, lots: int, lot_size: int,
              entry_price: Decimal, stop_price: Decimal, round_trip_charges: Decimal,
              buy_charges: Decimal, quote_ok: bool, quote_detail: str, expiry: date,
              bot_id: uuid.UUID | None = None, bot_risk_per_trade: Decimal | None = None,
              bot_square_off: str | None = None,
              account_blockers: list[str] | None = None) -> Decision:
    pos = open_positions(db, account.id)
    pend = pending_entries(db, account.id)
    acct = funds(db, account)
    available = acct["available"]
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
            Position.bot_id == bot_id, Position.account_id == account.id,
            Position.exit_reason == "STOP_LOSS",
            Position.closed_at >= datetime.combine(local.date(), time(0), IST))) > 0
    demo_open = (settings.market_data_mode == MarketDataMode.DEMO
                 and settings.demo_market_always_open)
    market_ok = True
    if settings.market_data_mode == MarketDataMode.INDSTOCKS:
        market_ok = session_state(now, settings.market_holidays).is_open

    blockers = account_blockers or []
    checks = [
        ("account", f"{account.mode} account can trade", not blockers, " ".join(blockers)),
        ("paper", "Paper trading enabled", settings.paper_trading_enabled
         or account.mode != "DEMO", "Paper trading is disabled."),
        ("kill_switch", "Kill switch off", not rs.kill_switch_engaged,
         "Kill switch is engaged."),
        ("reconcile", "No unresolved orders", unresolved_orders(db, account.id) == 0,
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
        ("funds", "Available funds", available is not None and cost <= available,
         f"Needs {cost:.2f}; available {available:.2f}." if available is not None
         else "No broker balance is available for this account."),
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
    if account.mode == "REAL":  # REAL entries also need a healthy broker connection
        from app.brokers.connections import connection_for_account, connection_health

        bh = connection_health(connection_for_account(db, account), now)
        checks.insert(1, ("broker", "Broker connection healthy", bh["ok"], bh["detail"]))
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
        "account_mode": account.mode,
        "available": str(available.quantize(D("0.01"))) if available is not None else None,
        "day_net": str(day_net.quantize(D("0.01"))), "entries_used": entries_used,
        "open_positions": len(pos), "pending_entries": len(pend),
    }
    return out
