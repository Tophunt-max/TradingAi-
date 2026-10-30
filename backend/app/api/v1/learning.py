from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.api.deps import AdminDep, DbDep, SettingsDep
from app.core.time import utcnow
from app.learning import drift, metrics
from app.learning.attribution import ERROR_CLASSES
from app.market_data.factory import ProviderDep
from app.models.intelligence import DecisionRecord, ModelDriftReport
from app.models.ml import ModelVersion
from app.schemas.common import Page, PageParams
from app.services.audit import record_audit
from app.workers import learning as worker

router = APIRouter(tags=["learning"])
SUMMARY_LIMIT = 5000


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    origin: str
    decision_ts: datetime
    bar_ts: datetime
    symbol: str
    timeframe: str
    horizon_bars: int
    close: float
    model_version_id: uuid.UUID | None
    model_name: str | None
    feature_version: str | None
    indicator_version: str | None
    data_quality: str
    data_source: str
    regime: str | None
    probabilities: dict[str, Any] | None
    uncertainty: float | None
    calibration_status: str
    predicted_direction: str | None
    decision: str
    no_trade_reasons: list[str]
    strategies: list[str]
    plan: dict[str, Any] | None
    event_risk: str | None
    order_id: uuid.UUID | None
    position_id: uuid.UUID | None
    account_mode: str | None
    outcome_status: str
    resolved_at: datetime | None
    realized_return: float | None
    realized_direction: str | None
    realized_volatility: float | None
    max_favorable_excursion: float | None
    max_adverse_excursion: float | None
    regime_at_resolution: str | None
    option_premium_change: float | None
    slippage: float | None
    error_classes: list[str]
    outcome_notes: list[str]


def _filtered(symbol: str | None, timeframe: str | None, decision: str | None = None,
              status: str | None = None) -> Any:
    stmt = select(DecisionRecord)
    if symbol:
        stmt = stmt.where(DecisionRecord.symbol == symbol.upper())
    if timeframe:
        stmt = stmt.where(DecisionRecord.timeframe == timeframe)
    if decision:
        stmt = stmt.where(DecisionRecord.decision == decision)
    if status:
        stmt = stmt.where(DecisionRecord.outcome_status == status)
    return stmt


@router.get("/learning/decisions", response_model=Page[DecisionOut])
def decisions(_: ProviderDep, db: DbDep, page: Annotated[PageParams, Depends()],
              symbol: Annotated[str | None, Query(max_length=32)] = None,
              timeframe: Annotated[str | None, Query(max_length=8)] = None,
              decision: Annotated[str | None, Query(max_length=20)] = None,
              status: Annotated[str | None, Query(max_length=12)] = None) -> Page[DecisionOut]:
    """Every recorded decision, including NO TRADE and skipped ones, newest first."""
    stmt = _filtered(symbol, timeframe, decision, status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(DecisionRecord.decision_ts.desc())
                      .limit(page.limit).offset(page.offset)).all()
    return Page(items=[DecisionOut.model_validate(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)


def _drift_out(r: ModelDriftReport | None, mv: ModelVersion) -> dict[str, Any]:
    base = {"model_id": str(mv.id), "model_name": mv.name, "model_status": mv.status}
    if r is None:
        return {**base, "status": "NOT_COMPUTED", "computed_at": None,
                "reasons": ["Drift has not been computed yet (run the learning worker)."]}
    return {**base, "status": r.status, "computed_at": r.computed_at,
            "reference_basis": r.reference_basis, "window_rows": r.window_rows,
            "max_feature_psi": r.max_feature_psi, "feature_psi": r.feature_psi,
            "prediction_psi": r.prediction_psi, "performance": r.performance,
            "reasons": r.reasons}


@router.get("/learning/drift")
def drift_status(_: ProviderDep, db: DbDep) -> dict[str, Any]:
    models = db.scalars(select(ModelVersion).where(
        ModelVersion.status.in_(("PROMOTED", "SHADOW"))).order_by(ModelVersion.name)).all()
    return {"thresholds": {"psi_moderate": drift.PSI_MODERATE,
                           "psi_significant": drift.PSI_SIGNIFICANT,
                           "performance_min_samples": drift.PERF_MIN_SAMPLES,
                           "performance_max_brier_ratio": drift.PERF_MAX_RATIO},
            "models": [_drift_out(drift.latest(db, m.id), m) for m in models]}


@router.get("/learning/summary")
def summary(_: ProviderDep, db: DbDep,
            symbol: Annotated[str | None, Query(max_length=32)] = None,
            timeframe: Annotated[str | None, Query(max_length=8)] = None) -> dict[str, Any]:
    rows = db.scalars(_filtered(symbol, timeframe).order_by(DecisionRecord.decision_ts.desc())
                      .limit(SUMMARY_LIMIT)).all()
    out = metrics.summary(list(rows))
    out["error_class_definitions"] = list(ERROR_CLASSES)
    out["drift"] = [{k: v for k, v in d.items() if k in ("model_id", "model_name", "status",
                                                         "model_status", "computed_at")}
                    for d in drift_status(_, db)["models"]]
    out["window"] = {"max_records": SUMMARY_LIMIT, "symbol": symbol, "timeframe": timeframe}
    return out


@router.post("/admin/learning/run")
def run_learning(ctx: AdminDep, db: DbDep, settings: SettingsDep, provider: ProviderDep,
                 request: Request) -> dict[str, Any]:
    """Run one learning-worker pass now (outcomes + due drift reports)."""
    stats = worker.run_once(db, settings, provider, utcnow())
    record_audit(db, action="learning.pass", user_id=ctx.user.id, request=request,
                 details={k: v for k, v in stats.items() if k != "drift"})
    db.commit()
    return stats
