"""Alerts feed and broker connection status.

Until the alert engine exists (Phase 5), alerts are derived from the user's
own audit trail (security, bot and risk events), so every item is real.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep
from app.models.audit import AuditLog

router = APIRouter(tags=["alerts"])

Category = Literal["TRADES", "BOTS", "RISK", "SECURITY", "SYSTEM"]

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
    "bot.start": ("BOTS", "Bot start rejected: engine unavailable", "warning"),
    "risk.limits_updated": ("RISK", "Risk limits changed", "warning"),
    "risk.kill_switch_engaged": ("RISK", "Kill switch engaged", "critical"),
    "risk.kill_switch_released": ("RISK", "Kill switch released", "warning"),
}


class AlertOut(BaseModel):
    id: str
    category: Category
    severity: Literal["info", "warning", "critical"]
    title: str
    body: str | None
    created_at: datetime


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
        cat, title, sev = _MAP[r.action]
        if category and cat != category:
            continue
        body = None
        if r.details.get("name"):
            body = str(r.details["name"])
        elif r.ip_address:
            body = f"From {r.ip_address}"
        out.append(AlertOut(id=str(r.id), category=cat, severity=sev, title=title, body=body,
                            created_at=r.created_at))
        if len(out) >= limit:
            break
    return out


class BrokerOut(BaseModel):
    id: str
    name: str
    kind: Literal["PAPER", "BROKER"]
    status: Literal["CONNECTED", "NOT_INTEGRATED"]
    supports_live_orders: bool
    note: str
    last_sync: datetime | None


@router.get("/brokers", response_model=list[BrokerOut])
def brokers(_: AuthDep) -> list[BrokerOut]:
    pending = ("No verified adapter yet. Integration is Phase 8 and uses only the broker's "
               "official API.")
    return [
        BrokerOut(id="paper", name="Paper Trading", kind="PAPER", status="CONNECTED",
                  supports_live_orders=False,
                  note="Simulated account (default). Fills start in Phase 6.", last_sync=None),
        *[
            BrokerOut(id=i, name=n, kind="BROKER", status="NOT_INTEGRATED",
                      supports_live_orders=False, note=pending, last_sync=None)
            for i, n in (("zerodha", "Zerodha Kite Connect"), ("angelone", "Angel One SmartAPI"),
                         ("upstox", "Upstox API"), ("dhan", "Dhan HQ API"))
        ],
    ]
