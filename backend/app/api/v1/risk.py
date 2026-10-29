from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.core.errors import AppError
from app.core.time import utcnow
from app.execution import risk as RISK
from app.execution import service as EX
from app.market_data.factory import build_provider
from app.schemas.risk import KillSwitchRequest, RiskLimitsOut, RiskLimitsUpdate, RiskStatus
from app.services import risk as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/risk", tags=["risk"])
logger = logging.getLogger("app.risk")


@router.get("", response_model=RiskStatus)
def risk_status(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> RiskStatus:
    rs = svc.get_or_create(db, ctx.user.id)
    out = svc.status(rs, db, settings)
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
    """Engaging blocks new entries at once, cancels pending entry orders and (by policy)
    sends exit orders for every open position. Works even if market data is down: exits
    that cannot fill yet are retried by the execution worker."""
    if not body.confirm:
        raise AppError("Confirm the kill-switch action.", code="CONFIRMATION_REQUIRED")
    rs = RISK.lock_user(db, ctx.user.id)
    svc.set_kill_switch(rs, body.engaged)
    actions: dict[str, int] = {"cancelled": 0, "exits": 0}
    if body.engaged:
        try:
            provider = build_provider(db, settings)
        except Exception:
            provider = _NoData()
        cache = EX.ChainCache(provider, settings)
        for o in RISK.pending_entries(db, ctx.user.id):
            EX.cancel(db, settings, cache, o, "Kill switch engaged.")
            actions["cancelled"] += 1
        if rs.square_off_on_kill_switch:
            now = utcnow()
            for p in RISK.open_positions(db, ctx.user.id):
                if p.status == "OPEN":
                    o = EX.place_exit(db, settings, cache, p, "KILL_SWITCH", now)
                    actions["exits"] += 1
                    if o is not None:
                        try:
                            EX.fill_now(db, settings, cache, o, now)
                        except Exception:
                            logger.warning("kill_switch_exit_fill_deferred")  # worker retries
    record_audit(db, action="risk.kill_switch_engaged" if body.engaged
                 else "risk.kill_switch_released", user_id=ctx.user.id, request=request,
                 details=actions)
    db.commit()
    out = svc.status(rs, db, settings)
    db.commit()
    return out


class _NoData:
    """Stand-in provider when market data is unavailable during an emergency stop."""

    name = "NONE"

    def __getattr__(self, _: str):  # type: ignore[no-untyped-def]
        def fail(*a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("Market data unavailable.")
        return fail
