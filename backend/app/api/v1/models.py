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


@router.post("/admin/models/train", response_model=ModelOut, status_code=201)
def train(body: TrainRequest, ctx: AdminDep, db: DbDep, settings: SettingsDep,
          request: Request) -> ModelOut:
    with provider_errors():
        mv = registry.train_candidate(db, build_provider(db, settings),
                                      symbol=body.symbol.upper(), timeframe=body.timeframe,
                                      horizon=body.horizon_bars, user_id=ctx.user.id)
    record_audit(db, action="model.trained", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(mv.id),
                 details={"status": mv.status, "gate_passed": mv.gate["passed"]})
    db.commit()
    return _out(mv)


def _get(db: DbDep, model_id: uuid.UUID) -> ModelVersion:
    m = db.get(ModelVersion, model_id)
    if m is None:
        raise NotFoundError("Model not found.")
    return m


@router.post("/admin/models/{model_id}/promote", response_model=ModelOut)
def promote(model_id: uuid.UUID, ctx: AdminDep, db: DbDep, request: Request) -> ModelOut:
    m = registry.promote(db, _get(db, model_id))
    record_audit(db, action="model.promoted", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(m.id))
    db.commit()
    return _out(m)


@router.post("/admin/models/{model_id}/retire", response_model=ModelOut)
def retire(model_id: uuid.UUID, ctx: AdminDep, db: DbDep, request: Request) -> ModelOut:
    m = registry.retire(_get(db, model_id))
    record_audit(db, action="model.retired", user_id=ctx.user.id, request=request,
                 entity_type="model", entity_id=str(m.id))
    db.commit()
    return _out(m)
