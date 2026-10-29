from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.accounts import core as ACC
from app.api.deps import AuthDep, DbDep, SettingsDep
from app.core.errors import AppError
from app.core.time import utcnow
from app.execution import risk as RISK
from app.execution import service as EX
from app.market_data.factory import build_provider
from app.models.accounts import Account
from app.schemas.risk import KillSwitchRequest, RiskLimitsOut, RiskLimitsUpdate, RiskStatus
from app.services import risk as svc
from app.services.accounts import real_unavailable_reasons
from app.services.audit import record_audit

router = APIRouter(prefix="/risk", tags=["risk"])
logger = logging.getLogger("app.risk")
ModeQuery = Annotated[Literal["DEMO", "REAL"] | None,
                      Query(description="Account whose usage to show; defaults to active.")]


def _status(db: DbDep, settings: SettingsDep, user_id: uuid.UUID,
            mode: str | None) -> RiskStatus:
    rs = svc.get_or_create(db, user_id)
    mode, acc = ACC.resolve(db, user_id, mode)
    reasons = real_unavailable_reasons(db, settings, acc)[:2] if mode == "REAL" else []
    return svc.status(rs, db, settings, account=acc, account_mode=mode,
                      account_reasons=reasons)


@router.get("", response_model=RiskStatus)
def risk_status(ctx: AuthDep, db: DbDep, settings: SettingsDep,
                account_mode: ModeQuery = None) -> RiskStatus:
    out = _status(db, settings, ctx.user.id, account_mode)
    db.commit()
    return out


@router.patch("/limits", response_model=RiskLimitsOut)
def update_limits(body: RiskLimitsUpdate, ctx: AuthDep, db: DbDep,
                  request: Request) -> RiskLimitsOut:
    rs = svc.get_or_create(db, ctx.user.id)
    changes = svc.apply_update(rs, body)
    record_audit(db, action="risk.limits_updated", user_id=ctx.user.id, request=request,
                 details=changes)
    db.commit()
    return RiskLimitsOut.model_validate(rs)


@router.post("/kill-switch", response_model=RiskStatus)
def kill_switch(body: KillSwitchRequest, ctx: AuthDep, db: DbDep, settings: SettingsDep,
                request: Request) -> RiskStatus:
    """Engaging blocks new entries at once in every account (the switch is part of the
    user's shared risk policy), cancels pending entry orders and (by policy) sends exit
    orders for every open position, account by account through each account's own adapter.
    Works even if market data is down: exits that cannot fill yet are retried by the
    execution worker. REAL accounts have no broker client in this build; any failure there
    is counted, never hidden."""
    if not body.confirm:
        raise AppError("Confirm the kill-switch action.", code="CONFIRMATION_REQUIRED")
    rs = RISK.lock_user(db, ctx.user.id)
    svc.set_kill_switch(rs, body.engaged)
    actions: dict[str, int] = {"cancelled": 0, "exits": 0, "failed": 0}
    if body.engaged:
        try:
            provider = build_provider(db, settings)
        except Exception:
            provider = _NoData()
        cache = EX.ChainCache(provider, settings)
        accounts = db.scalars(select(Account).where(Account.user_id == ctx.user.id)
                              .order_by(Account.mode)).all()
        for acc in accounts:
            for o in RISK.pending_entries(db, acc.id):
                try:
                    with db.begin_nested():
                        EX.cancel(db, settings, cache, o, "Kill switch engaged.")
                    actions["cancelled"] += 1
                except Exception:
                    actions["failed"] += 1
                    logger.warning("kill_switch_cancel_failed",
                                   extra={"account_mode": acc.mode})
            if rs.square_off_on_kill_switch:
                now = utcnow()
                for p in RISK.open_positions(db, acc.id):
                    if p.status == "OPEN":
                        o = EX.place_exit(db, settings, cache, p, "KILL_SWITCH", now)
                        actions["exits"] += 1
                        if o is not None:
                            try:
                                EX.fill_now(db, settings, cache, o, now)
                            except Exception:
                                logger.warning("kill_switch_exit_fill_deferred")  # retried
    record_audit(db, action="risk.kill_switch_engaged" if body.engaged
                 else "risk.kill_switch_released", user_id=ctx.user.id, request=request,
                 details=actions)
    db.commit()
    out = _status(db, settings, ctx.user.id, None)
    db.commit()
    return out


class _NoData:
    """Stand-in provider when market data is unavailable during an emergency stop."""

    name = "NONE"

    def __getattr__(self, _: str):  # type: ignore[no-untyped-def]
        def fail(*a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("Market data unavailable.")
        return fail
