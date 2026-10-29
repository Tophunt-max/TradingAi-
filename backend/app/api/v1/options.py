from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.api.deps import AuthDep, SettingsDep
from app.api.v1.market import Provenance, demo_provenance, require_demo
from app.core.errors import AppError, NotFoundError
from app.core.time import utcnow
from app.market_data import demo_options

router = APIRouter(prefix="/options", tags=["options"])

Underlying = Literal["NIFTY", "BANKNIFTY"]

METADATA_NOTE = (
    "Lot size, tick size and expiry calendar are not available: they must come from the "
    "authorised instrument master (Phase 3). DEMO expiries are illustrative dates only."
)


class OptionSide(BaseModel):
    ltp: float
    bid: float
    ask: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    oi: int
    oi_change: int
    volume: int


class ChainRow(BaseModel):
    strike: int
    is_atm: bool
    call: OptionSide
    put: OptionSide


class ChainPayload(Provenance):
    underlying: str
    underlying_name: str
    expiry: date
    spot: float
    atm_strike: int
    strike_step: int
    lot_size: int | None
    tick_size: float | None
    pcr: float | None
    metadata_note: str
    greeks_model: str
    rows: list[ChainRow]


class ExpiriesPayload(Provenance):
    underlying: str
    expiries: list[date]
    metadata_note: str


@router.get("/{underlying}/expiries", response_model=ExpiriesPayload)
def expiries(_: AuthDep, settings: SettingsDep,
             underlying: Annotated[Underlying, Path()]) -> ExpiriesPayload:
    require_demo(settings)
    now = utcnow()
    return ExpiriesPayload(**demo_provenance(now), underlying=underlying,
                           expiries=demo_options.demo_expiries(now), metadata_note=METADATA_NOTE)


@router.get("/{underlying}/chain", response_model=ChainPayload)
def chain(
    _: AuthDep,
    settings: SettingsDep,
    underlying: Annotated[Underlying, Path()],
    expiry: Annotated[date | None, Query()] = None,
    strikes: Annotated[int, Query(ge=3, le=20)] = 10,
) -> ChainPayload:
    require_demo(settings)
    now = utcnow()
    valid = demo_options.demo_expiries(now)
    exp = expiry or valid[0]
    if exp not in valid:
        raise AppError("Unknown expiry for this underlying.", code="INVALID_EXPIRY",
                       status_code=422, details=[d.isoformat() for d in valid])
    try:
        data = demo_options.chain(underlying, exp, now, strikes)
    except KeyError as exc:
        raise NotFoundError("Unknown underlying.") from exc
    return ChainPayload(**demo_provenance(now), **data, metadata_note=METADATA_NOTE,
                        greeks_model="Black-Scholes (r = 6.5%) on demo IV")
