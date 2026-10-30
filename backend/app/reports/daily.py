"""Daily report and P&L analytics for ONE account (formulas in docs/reports.md).

Basis: a *trade* is a position, attributed to the IST day it closed. For each trade
gross = realised gross P&L, charges = all charges of its fills (entry and exit),
net = gross - charges. DEMO trades come from simulated fills with estimated charges; REAL
trades come only from broker-confirmed fills (charges are estimates until reconciled).
Every query is filtered by account_id, so a DEMO report never contains REAL rows.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import IST, to_ist, utcnow
from app.models.accounts import Account
from app.models.audit import AuditLog
from app.models.bots import Bot
from app.models.brokers import BrokerChargeReconciliation
from app.models.execution import DailyRiskState, Fill, Order, Position

D = Decimal
ZERO = D("0")
CENT = D("0.01")
COMPONENTS = ("brokerage", "stt", "exchange_txn", "sebi", "stamp_duty", "gst")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MAX_RANGE_DAYS = 366

MODE_LABELS = {
    "DEMO": "DEMO · VIRTUAL",
    "REAL": "REAL · BROKER-CONFIRMED",
}
BASIS = {
    "DEMO": "Simulated fills on virtual money; charges are estimates from the versioned "
            "schedule.",
    "REAL": "Broker-confirmed fills only; charges are estimates until reconciled with the "
            "broker.",
}


def money(v: Decimal | None) -> str | None:
    return None if v is None else str(D(v).quantize(CENT, rounding=ROUND_HALF_UP))


def ist(dt: datetime | None) -> str:
    return to_ist(dt).isoformat(timespec="seconds") if dt else ""


def day_window(start: date, end: date | None = None) -> tuple[datetime, datetime]:
    """[start 00:00 IST, (end or start) + 1 day 00:00 IST) as aware datetimes."""
    lo = datetime.combine(start, time(0), IST)
    hi = datetime.combine((end or start) + timedelta(days=1), time(0), IST)
    return lo, hi


def closed_trades(db: Session, account: Account | None, lo: datetime,
                  hi: datetime) -> list[Position]:
    if account is None:
        return []
    return list(db.scalars(select(Position).where(
        Position.account_id == account.id, Position.status == "CLOSED",
        Position.closed_at >= lo, Position.closed_at < hi)
        .order_by(Position.closed_at, Position.id)).all())


def _bots(db: Session, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, Bot]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {b.id: b for b in db.scalars(select(Bot).where(Bot.id.in_(ids))).all()}


def drawdown(values: Iterable[Decimal]) -> tuple[Decimal, Decimal]:
    """(max drawdown, current drawdown) of a cumulative curve starting at 0."""
    peak = cum = ZERO
    worst = ZERO
    for v in values:
        cum += v
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst, peak - cum


def summarise(trades: list[Position]) -> dict[str, Any]:
    nets = [p.realized_gross - p.charges_total for p in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross = sum((p.realized_gross for p in trades), ZERO)
    charges = sum((p.charges_total for p in trades), ZERO)
    mdd, _ = drawdown(nets)
    loss_sum = -sum(losses, ZERO)
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "breakeven": len(nets) - len(wins) - len(losses),
        "win_rate_pct": money(D(len(wins)) * 100 / len(trades)) if trades else None,
        "gross": money(gross), "charges": money(charges), "net": money(gross - charges),
        "avg_win": money(sum(wins, ZERO) / len(wins)) if wins else None,
        "avg_loss": money(sum(losses, ZERO) / len(losses)) if losses else None,
        "best_trade": money(max(nets)) if nets else None,
        "worst_trade": money(min(nets)) if nets else None,
        "profit_factor": money(sum(wins, ZERO) / loss_sum) if loss_sum > 0 else None,
        "max_drawdown": money(mdd),
    }


def _group(trades: list[Position], key: Any) -> list[dict[str, Any]]:
    groups: dict[str, list[Position]] = defaultdict(list)
    for p in trades:
        groups[key(p)].append(p)
    rows = []
    for k, ps in groups.items():
        s = summarise(ps)
        rows.append({"key": k, "trades": s["trades"], "wins": s["wins"],
                     "win_rate_pct": s["win_rate_pct"], "gross": s["gross"],
                     "charges": s["charges"], "net": s["net"]})
    return sorted(rows, key=lambda r: (-r["trades"], r["key"]))


def charges_breakdown(db: Session, trades: list[Position]) -> dict[str, str]:
    """Charges of every fill of these trades (entry and exit), by component."""
    parts = {k: ZERO for k in COMPONENTS}
    total = ZERO
    ids = [p.id for p in trades]
    if ids:
        for f in db.scalars(select(Fill).join(Order, Order.id == Fill.order_id)
                            .where(Order.position_id.in_(ids))).all():
            for k in COMPONENTS:
                parts[k] += D(str(f.charges.get(k, "0")))
            total += f.charges_total
    out = {k: money(v) for k, v in parts.items()}
    out["total"] = money(total)
    return out  # type: ignore[return-value]


def slippage(db: Session, account: Account | None, lo: datetime,
             hi: datetime) -> dict[str, Any]:
    """Entry fills vs the reference price (the ask the risk manager priced the entry at).
    Positive = paid more than the reference. Exit orders carry no reference price."""
    if account is None:
        return {"orders": 0, "total": money(ZERO), "avg_per_unit": None, "avg_pct": None}
    orders = db.scalars(select(Order).where(
        Order.account_id == account.id, Order.purpose == "ENTRY", Order.filled_qty > 0,
        Order.created_at >= lo, Order.created_at < hi)).all()
    total, units, pct_sum, n = ZERO, 0, ZERO, 0
    for o in orders:
        ref = (o.risk_snapshot or {}).get("entry_price")
        fills = db.scalars(select(Fill).where(Fill.order_id == o.id)).all()
        qty = sum(f.quantity for f in fills)
        if not ref or not qty:
            continue
        ref_d = D(str(ref))
        avg = sum((f.price * f.quantity for f in fills), ZERO) / qty
        per_unit = avg - ref_d if o.side == "BUY" else ref_d - avg
        total += per_unit * qty
        units += qty
        n += 1
        if ref_d:
            pct_sum += per_unit / ref_d * 100
    return {"orders": n, "total": money(total),
            "avg_per_unit": money(total / units) if units else None,
            "avg_pct": money(pct_sum / n) if n else None}


def risk_events(db: Session, account: Account | None, day: date, lo: datetime,
                hi: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    counters: dict[str, Any] = {"entries": 0, "stop_loss_exits": 0, "fill_day_net": money(ZERO)}
    if account is None:
        return events, counters
    ds = db.scalar(select(DailyRiskState).where(DailyRiskState.account_id == account.id,
                                                DailyRiskState.trading_day == day))
    if ds is not None:
        counters = {"entries": ds.entries, "stop_loss_exits": ds.stop_loss_exits,
                    "fill_day_net": money(ds.realized_gross - ds.charges)}
        if ds.loss_limit_hit_at:
            events.append({"type": "LOSS_STOP", "at": ist(ds.loss_limit_hit_at),
                           "scope": account.mode,
                           "detail": "Daily loss stop reached; new entries blocked."})
        if ds.profit_lock_hit_at:
            events.append({"type": "PROFIT_LOCK", "at": ist(ds.profit_lock_hit_at),
                           "scope": account.mode,
                           "detail": "Daily profit lock reached; new entries blocked."})
    for a in db.scalars(select(AuditLog).where(
            AuditLog.user_id == account.user_id,
            AuditLog.action.in_(("risk.kill_switch_engaged", "risk.kill_switch_released")),
            AuditLog.created_at >= lo, AuditLog.created_at < hi)
            .order_by(AuditLog.created_at)).all():
        engaged = a.action.endswith("engaged")
        events.append({"type": "KILL_SWITCH_ENGAGED" if engaged else "KILL_SWITCH_RELEASED",
                       "at": ist(a.created_at), "scope": "ALL_ACCOUNTS",
                       "detail": "Kill switch engaged." if engaged else "Kill switch released."})
    events.sort(key=lambda e: e["at"])
    return events, counters


def _trade_row(p: Position, bots: dict[uuid.UUID, Bot]) -> dict[str, Any]:
    b = bots.get(p.bot_id) if p.bot_id else None
    return {"position_id": str(p.id), "instrument": p.trading_symbol,
            "underlying": p.underlying, "option_type": p.option_type, "qty": p.entry_qty,
            "entry_time_ist": ist(p.opened_at), "exit_time_ist": ist(p.closed_at),
            "avg_entry": money(p.avg_entry), "avg_exit": money(p.avg_exit),
            "gross": money(p.realized_gross), "charges": money(p.charges_total),
            "net": money(p.realized_gross - p.charges_total), "exit_reason": p.exit_reason,
            "bot_id": str(p.bot_id) if p.bot_id else None, "bot": b.name if b else None,
            "strategy": b.strategy_id if b else "MANUAL", "data_source": p.data_source}


def daily_report(db: Session, account: Account | None, mode: str, day: date,
                 now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    lo, hi = day_window(day)
    trades = closed_trades(db, account, lo, hi)
    bots = _bots(db, (p.bot_id for p in trades))
    events, counters = risk_events(db, account, day, lo, hi)
    fills = db.scalars(select(Fill).where(Fill.account_id == account.id, Fill.ts >= lo,
                                          Fill.ts < hi)).all() if account else []
    out: dict[str, Any] = {
        "account_mode": mode, "account_id": str(account.id) if account else None,
        "day": day.isoformat(), "timezone": "Asia/Kolkata", "mode_label": MODE_LABELS[mode],
        "pnl_basis": BASIS[mode], "complete": hi <= now,
        "totals": summarise(trades),
        "charges_breakdown": charges_breakdown(db, trades),
        "fills": {"count": len(fills),
                  "charges_paid": money(sum((f.charges_total for f in fills), ZERO))},
        "slippage": slippage(db, account, lo, hi),
        "by_bot": _group(trades, lambda p: bots[p.bot_id].name if p.bot_id in bots
                         else "Manual"),
        "by_strategy": _group(trades, lambda p: bots[p.bot_id].strategy_id
                              if p.bot_id in bots else "MANUAL"),
        "by_exit_reason": _group(trades, lambda p: p.exit_reason or "UNKNOWN"),
        "risk_events": events, "risk_counters": counters,
        "open_positions": len(db.scalars(select(Position.id).where(
            Position.account_id == account.id, Position.status != "CLOSED")).all())
        if account else 0,
        "trades": [_trade_row(p, bots) for p in trades],
        "charges_reconciliation": None, "generated_at": now.isoformat(),
    }
    if account is not None and account.mode == "REAL":
        rec = db.scalar(select(BrokerChargeReconciliation).where(
            BrokerChargeReconciliation.account_id == account.id,
            BrokerChargeReconciliation.trading_day == day))
        if rec is not None:
            out["charges_reconciliation"] = {
                "broker_reported": money(rec.broker_reported),
                "estimated": money(rec.estimated), "diff": money(rec.diff)}
    if account is None:
        out["note"] = "No account of this mode is connected; there is nothing to report."
    return out


def pnl_analytics(db: Session, account: Account | None, mode: str, start: date,
                  end: date) -> dict[str, Any]:
    lo, hi = day_window(start, end)
    trades = closed_trades(db, account, lo, hi)
    by_day: dict[date, list[Position]] = defaultdict(list)
    for p in trades:
        by_day[to_ist(p.closed_at).date()].append(p)  # type: ignore[arg-type]
    daily, cum, peak = [], ZERO, ZERO
    for d in sorted(by_day):
        s = summarise(by_day[d])
        cum += D(s["net"])
        peak = max(peak, cum)
        daily.append({"day": d.isoformat(), "weekday": WEEKDAYS[d.weekday()],
                      "trades": s["trades"], "gross": s["gross"], "charges": s["charges"],
                      "net": s["net"], "cumulative": money(cum), "drawdown": money(peak - cum)})
    mdd, cur = drawdown(D(r["net"]) for r in daily)
    nets = [(r["day"], D(r["net"])) for r in daily]
    weekday = _group(trades, lambda p: WEEKDAYS[to_ist(p.closed_at).weekday()])  # type: ignore[arg-type]
    weekday.sort(key=lambda r: WEEKDAYS.index(r["key"]))
    return {
        "account_mode": mode, "account_id": str(account.id) if account else None,
        "from": start.isoformat(), "to": end.isoformat(), "timezone": "Asia/Kolkata",
        "mode_label": MODE_LABELS[mode], "pnl_basis": BASIS[mode],
        "totals": summarise(trades), "daily": daily,
        "cumulative_net": money(cum), "max_drawdown": money(mdd),
        "current_drawdown": money(cur),
        "best_day": {"day": max(nets, key=lambda x: x[1])[0],
                     "net": money(max(n for _, n in nets))} if nets else None,
        "worst_day": {"day": min(nets, key=lambda x: x[1])[0],
                      "net": money(min(n for _, n in nets))} if nets else None,
        "by_weekday": weekday,
        "by_instrument": _group(trades, lambda p: f"{p.underlying} {p.option_type}"),
        "by_exit_reason": _group(trades, lambda p: p.exit_reason or "UNKNOWN"),
    }
