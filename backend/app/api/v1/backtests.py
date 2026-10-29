"""Backtesting Lab endpoints. Results are computed on request (not persisted)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.backtesting import service as svc
from app.backtesting import strategies as S
from app.backtesting.schemas import (
    BacktestRequest,
    MonteCarloRequest,
    SensitivityRequest,
    WalkForwardRequest,
)
from app.core.rate_limit import rate_limit
from app.market_data.factory import ProviderDep
from app.services import risk as risk_svc

router = APIRouter(prefix="/backtests", tags=["backtests"],
                   dependencies=[Depends(rate_limit("backtests", limit=60, window=60))])


def _limits(ctx: AuthDep, db: DbDep) -> Any:
    rs = risk_svc.get_or_create(db, ctx.user.id)  # read only; creates the default row once
    db.commit()
    return rs


@router.get("/strategies")
def strategies(_: AuthDep) -> list[dict[str, Any]]:
    return S.catalog()


@router.post("/run")
def run(body: BacktestRequest, ctx: AuthDep, db: DbDep, provider: ProviderDep,
        settings: SettingsDep) -> dict[str, Any]:
    report, _ = svc.run(body, db, ctx.user.id, _limits(ctx, db), provider, settings)
    return report


@router.post("/walk-forward")
def walk_forward(body: WalkForwardRequest, ctx: AuthDep, db: DbDep, provider: ProviderDep,
                 settings: SettingsDep) -> dict[str, Any]:
    return svc.walk_forward(body, db, ctx.user.id, _limits(ctx, db), provider, settings)


@router.post("/sensitivity")
def sensitivity(body: SensitivityRequest, ctx: AuthDep, db: DbDep, provider: ProviderDep,
                settings: SettingsDep) -> dict[str, Any]:
    return svc.sensitivity(body, db, ctx.user.id, _limits(ctx, db), provider, settings)


@router.post("/monte-carlo")
def monte_carlo(body: MonteCarloRequest, ctx: AuthDep, db: DbDep, provider: ProviderDep,
                settings: SettingsDep) -> dict[str, Any]:
    return svc.monte_carlo(body, db, ctx.user.id, _limits(ctx, db), provider, settings)
