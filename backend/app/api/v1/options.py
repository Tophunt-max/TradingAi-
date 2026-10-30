from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from app.accounts import core as ACC
from app.ai import registry
from app.analysis import context as C
from app.api.deps import AuthDep, DbDep, SettingsDep, user_rate_limit
from app.api.v1.market import Provenance
from app.core.errors import AppError
from app.market_data.factory import ProviderDep, provider_errors
from app.models.bots import RiskSettings
from app.options import intelligence as INTEL

router = APIRouter(prefix="/options", tags=["options"])
logger = logging.getLogger("app.options")

Underlying = Literal["NIFTY", "BANKNIFTY"]


class OptionSide(BaseModel):
    security_id: str | None = None
    trading_symbol: str | None = None
    ltp: float | None
    bid: float | None
    ask: float | None
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    oi: int | None
    oi_change: int | None
    volume: int | None


class ChainRow(BaseModel):
    strike: float
    is_atm: bool
    call: OptionSide | None
    put: OptionSide | None


class ChainPayload(Provenance):
    underlying: str
    underlying_name: str
    expiry: date
    spot: float | None
    atm_strike: float | None
    strike_step: float | None
    lot_size: int | None
    tick_size: float | None
    freeze_qty: int | None = None
    pcr: float | None
    metadata_note: str
    greeks_model: str
    rows: list[ChainRow]


class ExpiriesPayload(Provenance):
    underlying: str
    expiries: list[date]
    metadata_note: str


@router.get("/{underlying}/expiries", response_model=ExpiriesPayload)
def expiries(provider: ProviderDep,
             underlying: Annotated[Underlying, Path()]) -> ExpiriesPayload:
    with provider_errors():
        r = provider.expiries(underlying)
    return ExpiriesPayload(**r.provenance(), underlying=underlying, expiries=r.data,
                           metadata_note=" ".join(r.notes))


@router.get("/{underlying}/chain", response_model=ChainPayload)
def chain(
    provider: ProviderDep,
    underlying: Annotated[Underlying, Path()],
    expiry: Annotated[date | None, Query()] = None,
    strikes: Annotated[int, Query(ge=3, le=20)] = 10,
) -> ChainPayload:
    try:
        with provider_errors():
            r = provider.option_chain(underlying, expiry, strikes)
    except ValueError as exc:
        raise AppError("Unknown expiry for this underlying.", code="INVALID_EXPIRY",
                       status_code=422, details=exc.args[0] if exc.args else None) from exc
    note = (" ".join(r.notes) if r.data.get("lot_size") else
            "Lot size, tick size and expiry calendar are not available: they must come from the "
            "authorised instrument master. " + " ".join(r.notes))
    return ChainPayload(**r.provenance(), **r.data, metadata_note=note.strip())


class IntelligencePayload(Provenance):
    underlying: str
    expiry: date
    side: Literal["CE", "PE"]
    timeframe: str
    spot: float | None
    atm_strike: float | None
    data_source: str
    time_to_expiry: dict[str, Any]
    lot_size: int | None
    lot_size_source: str | None
    tick_size: float | None
    tick_size_source: str | None
    greeks_source: str
    greeks_model: str | None
    underlying_direction: dict[str, Any]
    underlying_context: dict[str, Any]
    iv_context: dict[str, Any]
    futures_basis: dict[str, Any]
    funds: dict[str, Any]
    candidates: list[dict[str, Any]]
    ranking: str | None = None
    assumptions: dict[str, Any]
    disclaimer_options: str


INTEL_DISCLAIMER = (
    "Research output for options BUYING only. The four outputs are separate on purpose: a "
    "correct view on the underlying can still lose money through theta, IV and spread. "
    "MODEL_ESTIMATE values come from a documented simulation, not from history, and never "
    "place or approve an order; the independent risk manager does that.")


def _direction(db: Any, underlying: str, timeframe: str, ctx: C.Ctx | None, source: str,
               quality: str) -> dict[str, Any]:
    mv = registry.serving(db, underlying, timeframe)
    if mv is None:
        return {"probabilities": None, "source": None,
                "reason": f"No promoted model for {underlying} {timeframe}; the direction "
                          "probability is not estimated."}
    if ctx is None or quality == "FAIL":
        return {"probabilities": None, "model_version": str(mv.id),
                "reason": "Underlying data unavailable or failed quality checks."}
    pred = registry.predict(mv, ctx, source)
    return {**pred, "source": "PROMOTED_MODEL"}


@router.get("/{underlying}/intelligence", response_model=IntelligencePayload,
            dependencies=[Depends(user_rate_limit("options_intel"))])
def intelligence(
    provider: ProviderDep,
    db: DbDep,
    auth: AuthDep,
    settings: SettingsDep,
    underlying: Annotated[Underlying, Path()],
    expiry: Annotated[date | None, Query()] = None,
    side: Annotated[Literal["CE", "PE"], Query()] = "CE",
    strikes: Annotated[int, Query(ge=0, le=5, description="Strikes each side of ATM")] = 2,
    strike: Annotated[float | None, Query(gt=0, description="Evaluate one strike only")] = None,
    stop_pct: Annotated[float, Query(gt=0, lt=100)] = 20.0,
    target_pct: Annotated[float, Query(gt=0, le=500)] = 30.0,
    horizon_minutes: Annotated[int, Query(ge=5, le=375)] = 60,
    timeframe: Annotated[Literal["5m", "15m", "1h"], Query()] = "15m",
) -> IntelligencePayload:
    try:
        with provider_errors():
            r = provider.option_chain(underlying, expiry, 10 if strike else max(strikes + 2, 5))
    except ValueError as exc:
        raise AppError("Unknown expiry for this underlying.", code="INVALID_EXPIRY",
                       status_code=422, details=exc.args[0] if exc.args else None) from exc
    others: list[dict[str, Any]] = []
    try:
        with provider_errors():
            exps = [e for e in provider.expiries(underlying).data if e != r.data["expiry"]][:2]
            others = [provider.option_chain(underlying, e, 1).data for e in exps]
    except Exception:  # term structure is optional context
        logger.warning("intelligence_term_structure_unavailable", extra={"u": underlying})
    ctx, cq = None, "FAIL"
    try:
        with provider_errors():
            cr = provider.candles(underlying, timeframe, 300)
        ctx, cq = C.build(cr.data, intraday=True, timeframe=timeframe), cr.quality.status
    except Exception:
        logger.warning("intelligence_candles_unavailable", extra={"u": underlying})
    direction = _direction(db, underlying, timeframe, ctx, r.source, cq)
    acc = ACC.get_account(db, auth.user.id, "DEMO")
    cash = ACC.cash(db, acc) if acc is not None else None
    rs = db.get(RiskSettings, auth.user.id)
    # Unconfigured users get the column default, which the risk manager applies too.
    risk = (rs.max_risk_per_trade if rs is not None
            else RiskSettings.__table__.c.max_risk_per_trade.default.arg)
    funds = {"account_mode": "DEMO",
             "available_funds": float(cash) if cash is not None else None,
             "available_funds_source": "DEMO_LEDGER" if cash is not None else
             "No DEMO account yet: affordability not checked.",
             "risk_per_trade": float(risk) if risk is not None else None,
             "risk_per_trade_source": "RISK_SETTINGS" if rs is not None else "DEFAULT_LIMIT"}
    out = INTEL.analyse(
        chain=r.data, source=r.source, quality_status=r.quality.status, side=side,
        now=r.as_of, strikes=strikes, strike=strike, stop_pct=stop_pct, target_pct=target_pct,
        horizon_minutes=horizon_minutes, ctx=ctx if cq != "FAIL" else None, direction=direction,
        available_funds=funds["available_funds"], risk_per_trade=funds["risk_per_trade"],
        brokerage=settings.paper_brokerage_per_order, demo_lot_size=settings.demo_lot_size,
        other_chains=others)
    notes = out.pop("notes")
    out.pop("source", None)
    out.pop("quality", None)
    prov = r.provenance()
    prov["notes"] = [*prov["notes"], *notes]
    return IntelligencePayload(**prov, **out, timeframe=timeframe, funds=funds,
                               disclaimer_options=INTEL_DISCLAIMER)
