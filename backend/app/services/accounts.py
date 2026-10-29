"""Account use-cases: listing with balances, REAL availability, mode switching, demo reset."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import core as ACC
from app.api.v1.system import live_blockers
from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.time import utcnow
from app.execution import risk as RISK
from app.execution import service as EX
from app.execution.state import move
from app.models.accounts import DEMO_OPENING_BALANCE, Account
from app.models.execution import ACTIVE_ORDER_STATES, Order, PaperBrokerOrder, Position
from app.models.user import UserPreferences
from app.services.auth import NOTIFICATION_KEYS

D = Decimal


def real_unavailable_reasons(db: Session, settings: Settings,
                             account: Account | None) -> list[str]:
    """Why the REAL mode cannot be used. Empty only when every condition holds; in this
    build `live_trading_enabled` is always false, so the list is never empty."""
    reasons = []
    if account is None:
        reasons.append("No real broker account is connected.")
    elif account.status != "ACTIVE":
        reasons.append(f"The real account is {account.status}.")
    if not settings.live_trading_enabled:
        reasons.append("Live trading is disabled in this build.")
        reasons += live_blockers(settings.market_data_mode)
    return reasons


def summary(db: Session, settings: Settings, user_id: uuid.UUID, mode: str) -> dict[str, Any]:
    acc = ACC.get_or_create_demo(db, user_id) if mode == "DEMO" else \
        ACC.get_account(db, user_id, mode)
    reasons = [] if mode == "DEMO" else real_unavailable_reasons(db, settings, acc)
    out: dict[str, Any] = {
        "id": str(acc.id) if acc else None, "mode": mode,
        "status": acc.status if acc else "NOT_CONNECTED",
        "broker": acc.broker if acc else None,
        "available": not reasons, "unavailable_reasons": reasons,
        "opening_balance": None, "cash": None, "available_funds": None, "equity": None,
        "used_premium": None, "unrealized_pnl": None, "realized_pnl_total": None,
        "reset_count": acc.reset_count if acc else 0,
        "last_reset_at": acc.last_reset_at if acc else None,
        "created_at": acc.created_at if acc else None,
    }
    if acc is None:
        out["note"] = "Not connected. Real balances come only from a verified broker."
        return out
    f = RISK.funds(db, acc)
    if acc.mode == "DEMO":
        out.update(opening_balance=f["opening_balance"], cash=f["cash"],
                   available_funds=f["available"], equity=f["equity"],
                   used_premium=f["used_margin"], unrealized_pnl=f["unrealized"],
                   realized_pnl_total=f["realized_net_total"],
                   note="Virtual money. Simulated fills, charges and P&L; never mixed with "
                        "real results.")
    else:
        out.update(realized_pnl_total=f["realized_net_total"],
                   note="No broker balance snapshot is available.")
    return out


def _prefs(db: Session, user_id: uuid.UUID) -> UserPreferences:
    prefs = db.get(UserPreferences, user_id)
    if prefs is None:
        prefs = UserPreferences(user_id=user_id,
                                notifications={k: True for k in NOTIFICATION_KEYS})
        db.add(prefs)
        db.flush()
    return prefs


def set_active_mode(db: Session, settings: Settings, user_id: uuid.UUID, mode: str) -> str:
    """Only changes which account the app shows and trades in. Orders and positions stay in
    the account they were created in."""
    if mode == "REAL":
        reasons = real_unavailable_reasons(db, settings, ACC.get_account(db, user_id, "REAL"))
        if reasons:
            raise ConflictError("The real account is not available: " + " ".join(reasons[:2]),
                                code="ACCOUNT_MODE_UNAVAILABLE", details={"reasons": reasons})
    else:
        ACC.get_or_create_demo(db, user_id)
    prefs = _prefs(db, user_id)
    previous = prefs.active_account_mode
    prefs.active_account_mode = mode
    return previous


def reset_demo(db: Session, settings: Settings, user_id: uuid.UUID,
               now: datetime | None = None) -> dict[str, Any]:
    """Reset the user's DEMO account (caller has the confirmation and commits).

    1. Every active DEMO order is cancelled at the paper simulator (any fills it already
       recorded are applied first, so nothing is lost) and moved to CANCELLED.
    2. Every open DEMO position is closed at its last known price (entry price if it was
       never marked), exit reason RESET, no simulated broker order and no charges. The
       proceeds are credited as RESET_CLOSE and the realised P&L recorded on the position.
    3. A RESET_ADJUSTMENT entry brings cash back to exactly the opening balance.
    4. Today's DEMO risk counters start fresh (previous values are returned for the audit
       record). Earlier days' rows are kept as history.
    Only rows of this user's DEMO account are read or written; REAL rows are never touched.
    """
    now = now or utcnow()
    RISK.lock_user(db, user_id)  # serialise with the execution pass and new entries
    acc = ACC.get_or_create_demo(db, user_id)
    if acc.mode != "DEMO":  # defensive; get_or_create_demo only returns DEMO
        raise ACC.AccountModeMismatch("Reset applies to DEMO accounts only.")
    cache = EX.ChainCache(_NoProvider(), settings)  # no market data needed, no fills made
    broker = EX.adapter_for(db, settings, acc, cache)
    cancelled = 0
    for o in db.scalars(select(Order).where(Order.account_id == acc.id,
                                            Order.status.in_(ACTIVE_ORDER_STATES))).all():
        st = None
        if o.broker_order_id:
            st = broker.cancel(o.broker_order_id)
        else:
            found = broker.find_by_client_id(o.client_order_id)
            if found is not None:
                o.broker_order_id = found.broker_order_id
                st = broker.cancel(found.broker_order_id)
        if st is not None:
            EX._sync_from_state(db, settings, o, st)
        if o.status in ("CREATED", "VALIDATED"):  # never reached the simulator
            move(db, o, "REJECTED", "Discarded by demo account reset.")
        elif o.status not in ("FILLED", "CANCELLED", "REJECTED"):
            move(db, o, "CANCELLED", "Cancelled by demo account reset.")
        cancelled += 1
    # Belt and braces: nothing may stay open at the simulator for this account.
    for pbo in db.scalars(select(PaperBrokerOrder).where(
            PaperBrokerOrder.account_id == acc.id,
            PaperBrokerOrder.status.in_(("OPEN", "PARTIAL")))).all():
        pbo.status, pbo.updated_at = "CANCELLED", now
    closed = []
    for p in db.scalars(select(Position).where(Position.account_id == acc.id,
                                               Position.status != "CLOSED")).all():
        mark = p.last_price if p.last_price is not None else p.avg_entry
        qty = p.quantity
        if qty > 0:
            ACC.post(db, acc, "RESET_CLOSE", mark * qty, ref_type="position", ref_id=str(p.id),
                     note=f"Reset: closed {qty} {p.trading_symbol} @ {mark:.2f} "
                          "(last known price, no broker order).", ts=now)
            prev = (p.avg_exit or D("0")) * p.exit_qty
            p.realized_gross += (mark - p.avg_entry) * qty
            p.exit_qty += qty
            p.avg_exit = ((prev + mark * qty) / p.exit_qty).quantize(D("0.0001"))
        p.quantity, p.status, p.exit_reason, p.closed_at = 0, "CLOSED", "RESET", now
        closed.append(str(p.id))
    cash_before = ACC.cash(db, acc) or D("0")
    opening = acc.opening_balance or DEMO_OPENING_BALANCE
    ACC.post(db, acc, "RESET_ADJUSTMENT", opening - cash_before, ref_type="reset",
             ref_id=str(acc.reset_count + 1),
             note=f"Reset #{acc.reset_count + 1}: balance restored to {opening:.2f}.", ts=now)
    ds = RISK.daily_state(db, acc, RISK.trading_day(now))
    previous_day = {"realized_gross": str(ds.realized_gross), "charges": str(ds.charges),
                    "entries": ds.entries, "stop_loss_exits": ds.stop_loss_exits,
                    "loss_limit_hit": ds.loss_limit_hit_at is not None,
                    "profit_lock_hit": ds.profit_lock_hit_at is not None}
    ds.realized_gross, ds.charges, ds.entries, ds.stop_loss_exits = D("0"), D("0"), 0, 0
    ds.loss_limit_hit_at = ds.profit_lock_hit_at = None
    acc.reset_count += 1
    acc.last_reset_at = now
    db.flush()
    return {"account_id": str(acc.id), "reset_count": acc.reset_count,
            "orders_cancelled": cancelled, "positions_closed": len(closed),
            "cash_before_adjustment": str(cash_before), "balance": str(ACC.cash(db, acc)),
            "previous_day_counters": previous_day}


class _NoProvider:
    """Reset never needs quotes; any market-data call fails instead of guessing a price."""

    name = "NONE"

    def __getattr__(self, _: str):  # type: ignore[no-untyped-def]
        def fail(*a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("Market data is not used by a demo reset.")
        return fail
