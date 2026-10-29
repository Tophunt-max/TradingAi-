from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import AuthDep, DbDep
from app.core.errors import AppError
from app.schemas.risk import KillSwitchRequest, RiskLimitsOut, RiskLimitsUpdate, RiskStatus
from app.services import risk as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("", response_model=RiskStatus)
def risk_status(ctx: AuthDep, db: DbDep) -> RiskStatus:
    rs = svc.get_or_create(db, ctx.user.id)
    db.commit()
    return svc.status(rs)


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
def kill_switch(body: KillSwitchRequest, ctx: AuthDep, db: DbDep,
                request: Request) -> RiskStatus:
    if not body.confirm:
        raise AppError("Confirm the kill-switch action.", code="CONFIRMATION_REQUIRED")
    rs = svc.get_or_create(db, ctx.user.id)
    svc.set_kill_switch(rs, body.engaged)
    record_audit(db, action="risk.kill_switch_engaged" if body.engaged
                 else "risk.kill_switch_released", user_id=ctx.user.id, request=request)
    db.commit()
    return svc.status(rs)
