from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai import registry
from app.api.deps import AdminDep, DbDep, SettingsDep
from app.core.errors import NotFoundError
from app.learning import promotion
from app.market_data.factory import ProviderDep, build_provider, provider_errors
from app.models.ml import ModelVersion, Prediction
from app.services.audit import record_audit

router = APIRouter(tags=["models"])


class ModelOut(BaseModel):
    id: uuid.UUID
    name: str
    symbol: str
    timeframe: str
    horizon_bars: int
    algorithm: str
    feature_version: str
    status: str
    data_source: str
    data_provider: str
    train_rows: int
    data_from: datetime
    data_to: datetime
    created_at: datetime
    promoted_at: datetime | None
    retired_at: datetime | None
    metrics: dict[str, Any]
    gate: dict[str, Any]
    shadow_started_at: datetime | None = None
    rolled_back_at: datetime | None = None


class PredictionOut(BaseModel):
    id: uuid.UUID
    model_version_id: uuid.UUID
    symbol: str
    timeframe: str
    horizon_bars: int
    bar_ts: datetime
    close: float
    p_down: float
    p_sideways: float
    p_up: float
    regime: str | None
    data_source: str
    outcome: str | None
    created_at: datetime


def _out(m: ModelVersion) -> ModelOut:
    return ModelOut(**{f: getattr(m, f) for f in ModelOut.model_fields})


@router.get("/models", response_model=list[ModelOut])
def list_models(_: ProviderDep, db: DbDep,
                symbol: Annotated[str | None, Query(max_length=32)] = None) -> list[ModelOut]:
    stmt = select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(100)
    if symbol:
        stmt = stmt.where(ModelVersion.symbol == symbol.upper())
    return [_out(m) for m in db.scalars(stmt).all()]


@router.get("/models/predictions", response_model=list[PredictionOut])
def predictions(_: ProviderDep, db: DbDep,
                symbol: Annotated[str | None, Query(max_length=32)] = None,
                limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[PredictionOut]:
    stmt = select(Prediction).order_by(Prediction.bar_ts.desc()).limit(limit)
    if symbol:
        stmt = stmt.where(Prediction.symbol == symbol.upper())
    return [PredictionOut(**{f: getattr(p, f) for f in PredictionOut.model_fields})
            for p in db.scalars(stmt).all()]


class CompareRow(BaseModel):
    id: uuid.UUID
    name: str
    algorithm: str
    model_type: str
    target: str
    horizon_bars: int
    status: str
    data_source: str
    created_at: datetime
    oos_samples: int | None
    log_loss: float | None
    brier: float | None
    ece: float | None
    accuracy: float | None
    climatology_log_loss: float | None
    climatology_brier: float | None
    vs_climatology: dict[str, Any] | None
    vs_logistic: dict[str, Any] | None
    better_than_climatology: bool | None
    better_than_logistic: bool | None
    calibration_status: str
    gate_passed: bool
    promotable: bool
    promotable_reason: str


class ComparePayload(BaseModel):
    symbol: str
    timeframe: str
    rows: list[CompareRow]
    criteria: str


COMPARE_CRITERIA = (
    "All metrics are out-of-sample (purged walk-forward). Lower log loss, Brier and ECE are "
    "better. A model counts as better than a baseline only when the 95% moving-block "
    "bootstrap CI of the per-prediction log-loss difference lies entirely below zero. "
    "Models trained on DEMO data are never promotable.")


def _compare_row(m: ModelVersion) -> CompareRow:
    mt = m.metrics or {}
    vc, vl = mt.get("vs_climatology"), mt.get("vs_logistic")
    passed = bool((m.gate or {}).get("passed"))
    demo = m.data_source == "DEMO"
    reason = ("Trained on synthetic DEMO data." if demo else
              "Passed the out-of-sample gate." if passed else "Did not pass the gate.")
    return CompareRow(
        id=m.id, name=m.name, algorithm=m.algorithm,
        model_type=(m.params or {}).get("model_type", "logistic"),
        target=(m.params or {}).get("target", "three_class"), horizon_bars=m.horizon_bars,
        status=m.status, data_source=m.data_source, created_at=m.created_at,
        oos_samples=mt.get("oos_samples"), log_loss=mt.get("log_loss"),
        brier=mt.get("brier"), ece=mt.get("ece"), accuracy=mt.get("accuracy"),
        climatology_log_loss=mt.get("baseline_log_loss"),
        climatology_brier=mt.get("baseline_brier"), vs_climatology=vc, vs_logistic=vl,
        better_than_climatology=vc["better"] if vc else None,
        better_than_logistic=vl["better"] if vl else None,
        calibration_status=((m.params or {}).get("calibration") or {}).get(
            "status", "UNCALIBRATED"),
        gate_passed=passed, promotable=passed and not demo, promotable_reason=reason)


@router.get("/models/compare", response_model=ComparePayload)
def compare(_: ProviderDep, db: DbDep, symbol: Annotated[str, Query(max_length=32)],
            timeframe: Annotated[Literal["5m", "15m", "1h", "1d"], Query()] = "15m"
            ) -> ComparePayload:
    rows = db.scalars(select(ModelVersion).where(
        ModelVersion.symbol == symbol.upper(), ModelVersion.timeframe == timeframe)
        .order_by(ModelVersion.created_at.desc()).limit(50)).all()
    out = sorted((_compare_row(m) for m in rows),
                 key=lambda r: (r.log_loss is None, r.log_loss or 0))
    return ComparePayload(symbol=symbol.upper(), timeframe=timeframe, rows=out,
                          criteria=COMPARE_CRITERIA)


@router.get("/models/{model_id}", response_model=ModelOut)
def get_model(model_id: uuid.UUID, _: ProviderDep, db: DbDep) -> ModelOut:
    m = db.get(ModelVersion, model_id)
    if m is None:
        raise NotFoundError("Model not found.")
    return _out(m)


class TrainRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: Literal["5m", "15m", "1h", "1d"] = "15m"
    horizon_bars: int | None = Field(default=None, ge=1, le=50)
    model_type: Literal["logistic", "random_forest", "hist_gb"] = "logistic"
    target: Literal["three_class", "binary"] = "three_class"
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


@router.post("/admin/models/train", response_model=ModelOut, status_code=201)
def train(body: TrainRequest, ctx: AdminDep, db: DbDep, settings: SettingsDep,
          request: Request) -> ModelOut:
    with provider_errors():
        mv = registry.train_candidate(db, build_provider(db, settings),
                                      symbol=body.symbol.upper(), timeframe=body.timeframe,
                                      horizon=body.horizon_bars, user_id=ctx.user.id,
                                      model_type=body.model_type, target=body.target,
                                      seed=body.seed)
    record_audit(db, action="model.trained", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(mv.id),
                 details={"status": mv.status, "gate_passed": mv.gate["passed"],
                          "model_type": body.model_type, "target": body.target})
    db.commit()
    return _out(mv)


def _get(db: DbDep, model_id: uuid.UUID) -> ModelVersion:
    m = db.get(ModelVersion, model_id)
    if m is None:
        raise NotFoundError("Model not found.")
    return m


@router.get("/models/{model_id}/promotion")
def promotion_status(model_id: uuid.UUID, _: ProviderDep, db: DbDep) -> dict[str, Any]:
    """Offline and shadow acceptance criteria for this model, check by check."""
    return promotion.status(db, _get(db, model_id))


@router.post("/admin/models/{model_id}/shadow", response_model=ModelOut)
def shadow(model_id: uuid.UUID, ctx: AdminDep, db: DbDep, request: Request) -> ModelOut:
    """CANDIDATE → SHADOW: predictions are recorded live but never shown or used."""
    m = promotion.to_shadow(db, _get(db, model_id))
    record_audit(db, action="model.shadow_started", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(m.id))
    db.commit()
    return _out(m)


@router.post("/admin/models/{model_id}/promote", response_model=ModelOut)
def promote(model_id: uuid.UUID, ctx: AdminDep, db: DbDep, request: Request) -> ModelOut:
    """SHADOW → PROMOTED, only if the offline and shadow criteria pass."""
    m = promotion.promote(db, _get(db, model_id))
    record_audit(db, action="model.promoted", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(m.id))
    db.commit()
    return _out(m)


@router.post("/admin/models/{model_id}/rollback", response_model=ModelOut)
def rollback(model_id: uuid.UUID, ctx: AdminDep, db: DbDep, request: Request) -> ModelOut:
    """Roll the PROMOTED model back and re-promote the previously promoted version."""
    rolled, prev = promotion.rollback(db, _get(db, model_id))
    record_audit(db, action="model.rolled_back", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(rolled.id),
                 details={"restored_model_id": str(prev.id)})
    db.commit()
    return _out(prev)


@router.post("/admin/models/{model_id}/retire", response_model=ModelOut)
def retire(model_id: uuid.UUID, ctx: AdminDep, db: DbDep, request: Request) -> ModelOut:
    m = registry.retire(_get(db, model_id))
    record_audit(db, action="model.retired", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(m.id))
    db.commit()
    return _out(m)
