from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.time import utcnow
from app.models.bots import RiskSettings
from app.schemas.risk import RiskLimitsOut, RiskLimitsUpdate, RiskStatus, RiskUsage

ENGINE_NOTE = (
    "The global risk manager and paper execution engine are not running yet (Phase 6). "
    "Limits are saved and enforced in bot validation; no orders are placed."
)
DISCLAIMER = (
    "Risk limits cannot guarantee a maximum loss during price gaps, illiquidity, broker "
    "outages or execution failures."
)


def get_or_create(db: Session, user_id: uuid.UUID) -> RiskSettings:
    rs = db.get(RiskSettings, user_id)
    if rs is None:
        rs = RiskSettings(user_id=user_id)
        db.add(rs)
        db.flush()
    return rs


def apply_update(rs: RiskSettings, body: RiskLimitsUpdate) -> dict[str, object]:
    changes = body.model_dump(exclude_none=True)
    merged = {c: getattr(rs, c) for c in RiskLimitsUpdate.model_fields} | changes
    capital = Decimal(merged["capital"])
    problems = []
    if Decimal(merged["max_risk_per_trade"]) > capital:
        problems.append("Risk per trade cannot exceed capital.")
    if Decimal(merged["daily_loss_limit"]) > capital:
        problems.append("Daily loss limit cannot exceed capital.")
    if Decimal(merged["max_risk_per_trade"]) > Decimal(merged["daily_loss_limit"]):
        problems.append("Risk per trade cannot exceed the daily loss limit.")
    if problems:
        raise AppError("Risk limits are inconsistent.", code="VALIDATION_ERROR", status_code=422,
                       details=problems)
    for k, v in changes.items():
        setattr(rs, k, v)
    return {k: str(v) for k, v in changes.items()}


def set_kill_switch(rs: RiskSettings, engaged: bool) -> None:
    rs.kill_switch_engaged = engaged
    rs.kill_switch_changed_at = utcnow()


def status(rs: RiskSettings) -> RiskStatus:
    zero = Decimal("0")
    reasons = ["Execution engine not available (Phase 6)."]
    if rs.kill_switch_engaged:
        reasons.insert(0, "Kill switch is engaged.")
    return RiskStatus(
        mode="PAPER",
        engine_active=False,
        engine_note=ENGINE_NOTE,
        limits=RiskLimitsOut.model_validate(rs),
        usage=RiskUsage(
            realized_pnl=zero, unrealized_pnl=zero, estimated_charges=zero, entries_used=0,
            open_positions=0, planned_risk=zero, daily_loss_used_pct=0.0,
            profit_lock_reached=False,
        ),
        new_entries_allowed=False,
        block_reasons=reasons,
        disclaimer=DISCLAIMER,
    )
