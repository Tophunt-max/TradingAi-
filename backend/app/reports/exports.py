"""CSV exports for one account. Timestamps are IST (ISO 8601 with +05:30); every row
carries `account_mode`. Column lists are documented in docs/reports.md."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounts import Account, LedgerEntry
from app.models.execution import DailyRiskState, Fill, Order
from app.reports.daily import COMPONENTS, ist, money, pnl_analytics

ORDER_COLUMNS = ["account_mode", "created_at_ist", "order_id", "client_order_id", "broker",
                 "broker_order_id", "purpose", "side", "instrument", "qty", "order_type",
                 "limit_price", "status", "filled_qty", "avg_fill_price", "exit_reason",
                 "reject_reason", "bot_id", "data_source", "terminal_at_ist"]
FILL_COLUMNS = ["account_mode", "ts_ist", "fill_id", "order_id", "broker", "broker_fill_id",
                "side", "instrument", "qty", "price", "value", *COMPONENTS, "charges_total"]
LEDGER_COLUMNS = ["account_mode", "ts_ist", "seq", "kind", "amount", "balance_after",
                  "ref_type", "ref_id", "note"]
DAILY_COLUMNS = ["account_mode", "day", "trades", "gross", "charges", "net", "cumulative",
                 "drawdown", "loss_stop_hit", "profit_lock_hit", "entries", "stop_loss_exits"]


def to_csv(header: list[str], rows: Iterable[list[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def orders_csv(db: Session, account: Account | None) -> str:
    rows = db.scalars(select(Order).where(Order.account_id == account.id)
                      .order_by(Order.created_at)).all() if account else []
    return to_csv(ORDER_COLUMNS, ([
        o.account_mode, ist(o.created_at), o.id, o.client_order_id, o.broker, o.broker_order_id,
        o.purpose, o.side, o.trading_symbol, o.quantity, o.order_type, o.limit_price, o.status,
        o.filled_qty, o.avg_fill_price, o.exit_reason, o.reject_reason, o.bot_id,
        o.data_source, ist(o.terminal_at)] for o in rows))


def fills_csv(db: Session, account: Account | None) -> str:
    rows = db.execute(select(Fill, Order.trading_symbol).join(Order, Order.id == Fill.order_id)
                      .where(Fill.account_id == account.id).order_by(Fill.ts)).all() \
        if account else []
    return to_csv(FILL_COLUMNS, ([
        f.account_mode, ist(f.ts), f.id, f.order_id, f.broker, f.broker_fill_id, f.side, sym,
        f.quantity, f.price, money(f.price * f.quantity),
        *[f.charges.get(k, "0") for k in COMPONENTS], f.charges_total] for f, sym in rows))


def ledger_csv(db: Session, account: Account) -> str:
    """DEMO only: REAL accounts have no ledger (the caller refuses REAL)."""
    rows = db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == account.id)
                      .order_by(LedgerEntry.seq)).all()
    return to_csv(LEDGER_COLUMNS, ([
        e.account_mode, ist(e.created_at), e.seq, e.kind, e.amount, e.balance_after,
        e.ref_type, e.ref_id, e.note] for e in rows))


def daily_csv(db: Session, account: Account | None, mode: str, start: date, end: date) -> str:
    a = pnl_analytics(db, account, mode, start, end)
    states = {s.trading_day.isoformat(): s for s in db.scalars(select(DailyRiskState).where(
        DailyRiskState.account_id == account.id, DailyRiskState.trading_day >= start,
        DailyRiskState.trading_day <= end)).all()} if account else {}
    days = {r["day"]: r for r in a["daily"]}
    keys = sorted(set(days) | set(states))
    out, cum, dd = [], "0.00", "0.00"
    for k in keys:
        r = days.get(k, {})
        s = states.get(k)
        cum, dd = r.get("cumulative", cum), r.get("drawdown", dd)  # carried over no-trade days
        out.append([mode, k, r.get("trades", 0), r.get("gross", "0.00"),
                    r.get("charges", "0.00"), r.get("net", "0.00"), cum,
                    dd, bool(s and s.loss_limit_hit_at),
                    bool(s and s.profit_lock_hit_at), s.entries if s else 0,
                    s.stop_loss_exits if s else 0])
    return to_csv(DAILY_COLUMNS, out)
