from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.api.v1.market import Provenance
from app.core.errors import AppError
from app.market_data.factory import ProviderDep, provider_errors

router = APIRouter(prefix="/options", tags=["options"])

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
