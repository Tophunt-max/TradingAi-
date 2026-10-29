from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.market_data.factory import ProviderDep, build_provider, provider_errors

router = APIRouter(prefix="/market", tags=["market"])

Source = Literal["DEMO", "LIVE", "DELAYED"]


class QualityIssue(BaseModel):
    code: str
    severity: Literal["INFO", "WARN", "FAIL"]
    message: str


class Quality(BaseModel):
    status: Literal["OK", "WARN", "FAIL"]
    issues: list[QualityIssue]


class Provenance(BaseModel):
    """Every market-data response states where the data came from, when, and its quality."""

    source: Source
    provider: str
    is_demo: bool
    is_live: bool
    as_of: datetime
    disclaimer: str | None
    quality: Quality
    notes: list[str]


class IndexQuote(BaseModel):
    symbol: str
    name: str
    exchange: str
    last: Decimal | None
    prev_close: Decimal | None
    change: Decimal | None
    change_pct: Decimal | None


class MarketPayload(Provenance):
    items: list[IndexQuote]


class Quote(IndexQuote):
    segment: str
    sector: str | None
    day_high: Decimal | None
    day_low: Decimal | None
    volume: int | None
    relative_volume: float | None
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


def _q2(v: Any) -> Decimal | None:
    return Decimal(str(v)).quantize(Decimal("0.01")) if v is not None else None


def _quote(d: dict[str, Any]) -> Quote:
    d = {**d}
    for k in ("last", "prev_close", "change", "change_pct", "day_high", "day_low"):
        d[k] = _q2(d.get(k))
    return Quote(**d)


@router.get("/indices", response_model=MarketPayload)
def indices(provider: ProviderDep) -> MarketPayload:
    with provider_errors():
        r = provider.quotes(None, "INDEX")
    return MarketPayload(**r.provenance(), items=[IndexQuote(**_quote(q).model_dump(
        include=set(IndexQuote.model_fields))) for q in r.data])


@router.get("/quotes", response_model=QuotesPayload)
def quotes(
    provider: ProviderDep,
    segment: Annotated[Literal["ALL", "INDEX", "EQ"], Query()] = "ALL",
    symbols: Annotated[str | None, Query(max_length=400)] = None,
) -> QuotesPayload:
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    with provider_errors():
        r = provider.quotes(wanted, segment)
    return QuotesPayload(**r.provenance(), items=[_quote(q) for q in r.data])


@router.get("/movers", response_model=MoversPayload)
def movers(provider: ProviderDep,
           limit: Annotated[int, Query(ge=1, le=20)] = 5) -> MoversPayload:
    with provider_errors():
        r = provider.quotes(None, "EQ")
    eq = [_quote(q) for q in r.data if q.get("change_pct") is not None]
    by_chg = sorted(eq, key=lambda q: q.change_pct or 0, reverse=True)
    vol_key = (lambda q: q.relative_volume or 0) if any(q.relative_volume for q in eq) \
        else (lambda q: q.volume or 0)
    return MoversPayload(
        **r.provenance(),
        gainers=[q for q in by_chg if (q.change_pct or 0) > 0][:limit],
        losers=[q for q in reversed(by_chg) if (q.change_pct or 0) < 0][:limit],
        volume_leaders=sorted(eq, key=vol_key, reverse=True)[:limit],
    )


@router.get("/search", response_model=list[SearchHit])
def search(provider: ProviderDep,
           q: Annotated[str, Query(min_length=1, max_length=40)]) -> list[SearchHit]:
    with provider_errors():
        return [SearchHit(**h) for h in provider.search(q)]


class ProviderStatus(BaseModel):
    mode: str
    provider: str
    status: str
    detail: str | None = None
    kind: str | None = None
    instrument_sync: dict[str, str | None] | None = None
    price_feed_heartbeat: str | None = None


@router.get("/provider-status", response_model=ProviderStatus)
def provider_status(_: AuthDep, db: DbDep, settings: SettingsDep) -> ProviderStatus:
    with provider_errors():
        st = build_provider(db, settings).status()
    return ProviderStatus(mode=settings.market_data_mode.value, **st)
