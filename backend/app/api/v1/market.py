from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import AuthDep, SettingsDep
from app.core.config import MarketDataMode, Settings
from app.core.errors import ServiceUnavailableError
from app.core.time import utcnow
from app.market_data import demo

router = APIRouter(prefix="/market", tags=["market"])

Source = Literal["DEMO", "LIVE", "DELAYED"]


def require_demo(settings: Settings) -> None:
    if settings.market_data_mode != MarketDataMode.DEMO:
        raise ServiceUnavailableError(
            "No authorised market-data provider is configured.", code="MARKET_DATA_UNAVAILABLE"
        )


class Provenance(BaseModel):
    """Every market-data response states where the data came from and when."""

    source: Source
    is_demo: bool
    is_live: bool
    as_of: datetime
    disclaimer: str | None


def demo_provenance(now: datetime) -> dict[str, object]:
    return {"source": "DEMO", "is_demo": True, "is_live": False, "as_of": now,
            "disclaimer": demo.DEMO_DISCLAIMER}


class IndexQuote(BaseModel):
    symbol: str
    name: str
    exchange: str
    last: Decimal
    prev_close: Decimal
    change: Decimal
    change_pct: Decimal


class MarketPayload(Provenance):
    items: list[IndexQuote]


class Quote(IndexQuote):
    segment: str
    sector: str | None
    day_high: Decimal
    day_low: Decimal
    volume: int | None
    relative_volume: float
    has_options: bool


class QuotesPayload(Provenance):
    items: list[Quote]


class MoversPayload(Provenance):
    gainers: list[Quote]
    losers: list[Quote]
    volume_leaders: list[Quote]


class SearchHit(BaseModel):
    symbol: str
    name: str
    exchange: str
    segment: str
    has_options: bool


@router.get("/indices", response_model=MarketPayload)
def indices(_: AuthDep, settings: SettingsDep) -> MarketPayload:
    require_demo(settings)
    now = utcnow()
    return MarketPayload(**demo_provenance(now),
                         items=[IndexQuote(**q) for q in demo.demo_index_quotes(now)])


@router.get("/quotes", response_model=QuotesPayload)
def quotes(
    _: AuthDep,
    settings: SettingsDep,
    segment: Annotated[Literal["ALL", "INDEX", "EQ"], Query()] = "ALL",
    symbols: Annotated[str | None, Query(max_length=400)] = None,
) -> QuotesPayload:
    require_demo(settings)
    now = utcnow()
    wanted = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    out = []
    for sym, inst in demo.INSTRUMENTS.items():
        if wanted is not None and sym not in wanted:
            continue
        if segment != "ALL" and inst.segment != segment:
            continue
        out.append(Quote(**demo.quote(sym, now)))
    return QuotesPayload(**demo_provenance(now), items=out)


@router.get("/movers", response_model=MoversPayload)
def movers(_: AuthDep, settings: SettingsDep,
           limit: Annotated[int, Query(ge=1, le=20)] = 5) -> MoversPayload:
    require_demo(settings)
    now = utcnow()
    eq = [Quote(**demo.quote(s, now)) for s, i in demo.INSTRUMENTS.items() if i.segment == "EQ"]
    by_chg = sorted(eq, key=lambda q: q.change_pct, reverse=True)
    return MoversPayload(
        **demo_provenance(now),
        gainers=[q for q in by_chg if q.change_pct > 0][:limit],
        losers=[q for q in reversed(by_chg) if q.change_pct < 0][:limit],
        volume_leaders=sorted(eq, key=lambda q: q.relative_volume, reverse=True)[:limit],
    )


@router.get("/search", response_model=list[SearchHit])
def search(_: AuthDep, settings: SettingsDep,
           q: Annotated[str, Query(min_length=1, max_length=40)]) -> list[SearchHit]:
    require_demo(settings)
    return [SearchHit(symbol=i.symbol, name=i.name, exchange=i.exchange, segment=i.segment,
                      has_options=i.has_options) for i in demo.search(q)]
