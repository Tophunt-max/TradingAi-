"""Alerts feed.

Alerts are derived from the user's own records (audit trail incl. broker connection events,
bot signals and errors, orders, daily risk state and persisted daily reports), so every
item is real. Broker connection endpoints live in brokers.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.accounts import core as ACC
from app.api.deps import AuthDep, DbDep
from app.models.audit import AuditLog
from app.models.bots import Bot, BotEvent, BotRun, BotSignal
from app.models.execution import DailyRiskState, Order
from app.models.reports import DailyReport

router = APIRouter(tags=["alerts"])

Category = Literal["TRADES", "SIGNALS", "BOTS", "RISK", "SECURITY", "SYSTEM"]

_MAP: dict[str, tuple[Category, str, str]] = {
    "auth.session_created": ("SECURITY", "New sign-in", "info"),
    "auth.lockout": ("SECURITY", "Account temporarily locked", "warning"),
    "auth.refresh_reuse_detected": ("SECURITY", "Suspicious session activity", "critical"),
    "auth.password_reset": ("SECURITY", "Password was reset", "warning"),
    "user.password_change": ("SECURITY", "Password changed", "info"),
    "bot.created": ("BOTS", "Bot created", "info"),
    "bot.updated": ("BOTS", "Bot configuration updated", "info"),
    "bot.cloned": ("BOTS", "Bot cloned", "info"),
    "bot.deleted": ("BOTS", "Bot deleted", "info"),
    "bot.start": ("BOTS", "Bot started", "info"),
    "bot.pause": ("BOTS", "Bot paused", "info"),
    "bot.resume": ("BOTS", "Bot resumed", "info"),
    "bot.stop": ("BOTS", "Bot stopped", "info"),
    "risk.limits_updated": ("RISK", "Risk limits changed", "warning"),
    "risk.kill_switch_engaged": ("RISK", "Kill switch engaged", "critical"),
    "risk.kill_switch_released": ("RISK", "Kill switch released", "warning"),
    "broker.connect": ("SYSTEM", "Broker connected (read-only)", "info"),
    "broker.disconnect": ("SYSTEM", "Broker disconnected", "warning"),
    "broker.sync": ("SYSTEM", "Broker sync failed", "warning"),
}


class AlertOut(BaseModel):
    id: str
    category: Category
    severity: Literal["info", "warning", "critical"]
    title: str
    body: str | None
    created_at: datetime


def _kind(o: Order) -> str:
    return "Paper" if o.account_mode == "DEMO" else "Live"


@router.get("/alerts", response_model=list[AlertOut])
def alerts(
    ctx: AuthDep, db: DbDep,
    category: Annotated[Category | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AlertOut]:
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.user_id == ctx.user.id, AuditLog.action.in_(list(_MAP)))
        .order_by(AuditLog.created_at.desc())
        .limit(200)
    ).all()
    out = []
    for r in rows:
        if r.action == "auth.session_created" and r.outcome != "SUCCESS":
            continue
        if r.action == "broker.sync" and r.outcome == "SUCCESS":
            continue  # only failed syncs are worth an alert
        cat, title, sev = _MAP[r.action]
        if r.action.startswith("bot.") and r.outcome == "REJECTED":
            title, sev = f"{title.split(' (')[0]} rejected by activation checks", "warning"
        if r.action in ("broker.connect", "broker.disconnect") and r.outcome == "REJECTED":
            title = ("Broker connection failed" if r.action == "broker.connect"
                     else "Broker disconnect failed")
            sev = "warning"
        if category and cat != category:
            continue
        body = None
        if r.details.get("name"):
            body = str(r.details["name"])
        elif r.action.startswith("broker."):
            body = str(r.details.get("broker", "")) + (
                f" · {r.details['code']}" if r.details.get("code") else "")
        elif r.ip_address:
            body = f"From {r.ip_address}"
        out.append(AlertOut(id=str(r.id), category=cat, severity=sev, title=title, body=body,
                            created_at=r.created_at))
        if len(out) >= limit:
            break
    if category in (None, "SIGNALS"):
        sigs = db.execute(
            select(BotSignal, Bot.name, BotRun.mode).join(Bot, Bot.id == BotSignal.bot_id)
            .join(BotRun, BotRun.id == BotSignal.run_id)
            .where(BotSignal.user_id == ctx.user.id)
            .order_by(BotSignal.created_at.desc()).limit(limit)).all()
        out += [AlertOut(id=str(sg.id), category="SIGNALS", severity="info",
                         title=f"{sg.side} signal: {name}",
                         body=f"{sg.underlying} {sg.timeframe} at {sg.price:.2f}"
                              + (" (DEMO data)" if sg.data_source == "DEMO" else "")
                              + (". Sent to the paper risk manager." if mode == "PAPER"
                                 else ". Signal-only: no order placed."),
                         created_at=sg.created_at) for sg, name, mode in sigs]
    if category in (None, "BOTS"):
        errs = db.execute(
            select(BotEvent, Bot.name).join(Bot, Bot.id == BotEvent.bot_id)
            .where(Bot.user_id == ctx.user.id, BotEvent.level == "ERROR")
            .order_by(BotEvent.ts.desc()).limit(limit)).all()
        out += [AlertOut(id=str(e.id), category="BOTS", severity="warning",
                         title=f"Bot error: {name}", body=e.message, created_at=e.ts)
                for e, name in errs]
    # Trade and risk alerts come from the active account only (never mixed across modes).
    active = ACC.active_mode(db, ctx.user.id)
    if category in (None, "TRADES"):
        rows_o = db.scalars(
            select(Order).where(Order.user_id == ctx.user.id, Order.account_mode == active,
                                Order.status.in_(("FILLED", "REJECTED", "RECONCILIATION_REQUIRED")))
            .order_by(Order.updated_at.desc()).limit(limit)).all()
        for o in rows_o:
            if o.status == "FILLED":
                title = f"{_kind(o)} {o.side} filled: {o.trading_symbol}"
                body = f"{o.filled_qty} @ {o.avg_fill_price:.2f}" + (
                    f" · exit: {o.exit_reason}" if o.exit_reason else "")
                sev: str = "info"
            elif o.status == "REJECTED":
                title, body, sev = (f"{_kind(o)} {o.side} rejected: {o.trading_symbol}",
                                    o.reject_reason, "warning")
            else:
                title, body, sev = (f"Order needs reconciliation: {o.trading_symbol}",
                                    "New entries are blocked until this is resolved.", "critical")
            out.append(AlertOut(id=str(o.id), category="TRADES", severity=sev,  # type: ignore[arg-type]
                                title=title, body=body,
                                created_at=o.terminal_at or o.updated_at))
    if category in (None, "RISK"):
        for ds in db.scalars(select(DailyRiskState).where(DailyRiskState.user_id == ctx.user.id,
                                                          DailyRiskState.account_mode == active)
                             .order_by(DailyRiskState.trading_day.desc()).limit(10)).all():
            if ds.loss_limit_hit_at:
                out.append(AlertOut(id=f"loss-{ds.id}", category="RISK", severity="critical",
                                    title="Daily loss stop reached",
                                    body=f"Net {ds.realized_net:.2f} on {ds.trading_day}. "
                                         "New entries blocked for the day.",
                                    created_at=ds.loss_limit_hit_at))
            if ds.profit_lock_hit_at:
                out.append(AlertOut(id=f"profit-{ds.id}", category="RISK", severity="info",
                                    title="Daily profit lock reached",
                                    body=f"Net {ds.realized_net:.2f} on {ds.trading_day}. "
                                         "New entries blocked; open positions keep SL/target.",
                                    created_at=ds.profit_lock_hit_at))
    if category in (None, "SYSTEM"):
        for rep in db.scalars(select(DailyReport).where(DailyReport.user_id == ctx.user.id,
                                                        DailyReport.account_mode == active)
                              .order_by(DailyReport.day.desc()).limit(10)).all():
            t = rep.payload.get("totals", {})
            out.append(AlertOut(
                id=f"report-{rep.id}", category="SYSTEM", severity="info",
                title=f"Daily report ready: {rep.day.isoformat()}",
                body=f"{rep.payload.get('mode_label', rep.account_mode)}: "
                     f"{t.get('trades', 0)} trade(s), net {t.get('net', '0')}.",
                created_at=rep.generated_at))
    out.sort(key=lambda a: a.created_at, reverse=True)
    return out[:limit]
