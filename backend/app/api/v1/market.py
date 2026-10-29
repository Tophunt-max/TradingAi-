from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AuthDep, SettingsDep
from app.core.config import MarketDataMode
from app.core.errors import ServiceUnavailableError
from app.core.time import utcnow
from app.market_data.demo import DEMO_DISCLAIMER, demo_index_quotes

router = APIRouter(prefix="/market", tags=["market"])


class IndexQuote(BaseModel):
    symbol: str
    name: str
    exchange: str
    last: Decimal
    prev_close: Decimal
    change: Decimal
    change_pct: Decimal


class MarketPayload(BaseModel):
    """Envelope for every market-data response. Freshness is always explicit."""

    source: Literal["DEMO", "LIVE", "DELAYED"]
    is_demo: bool
    is_live: bool
    as_of: datetime
    disclaimer: str | None
    items: list[IndexQuote]


@router.get("/indices", response_model=MarketPayload)
def indices(_: AuthDep, settings: SettingsDep) -> MarketPayload:
    if settings.market_data_mode != MarketDataMode.DEMO:
        raise ServiceUnavailableError(
            "No authorised market-data provider is configured.", code="MARKET_DATA_UNAVAILABLE"
        )
    now = utcnow()
    return MarketPayload(
        source="DEMO",
        is_demo=True,
        is_live=False,
        as_of=now,
        disclaimer=DEMO_DISCLAIMER,
        items=[IndexQuote(**q) for q in demo_index_quotes(now)],  # type: ignore[arg-type]
    )
