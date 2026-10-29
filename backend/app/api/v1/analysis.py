from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.analysis.technical_summary import summarize
from app.api.v1.market import Provenance
from app.core.errors import ServiceUnavailableError
from app.market_data.factory import ProviderDep, provider_errors

router = APIRouter(prefix="/analysis", tags=["analysis"])


class Signal(BaseModel):
    name: str
    reading: str
    bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]


class AnalysisPayload(Provenance):
    symbol: str
    timeframe: str
    last_price: float
    trend: Literal["BULLISH", "BEARISH", "SIDEWAYS"]
    trend_method: str
    signals: list[Signal]
    support: list[float]
    resistance: list[float]
    atr: float | None
    rsi: float | None
    probabilities: dict[str, float] | None
    model_version: str | None
    decision: Literal["NO_TRADE", "TRADE_PROPOSAL"]
    abstain_reasons: list[str]


@router.get("/{symbol}", response_model=AnalysisPayload)
def analyse(
    provider: ProviderDep,
    symbol: Annotated[str, Path(max_length=32)],
    timeframe: Annotated[Literal["5m", "15m", "1h", "1d"], Query()] = "15m",
) -> AnalysisPayload:
    with provider_errors():
        r = provider.candles(symbol.upper(), timeframe, 150)
    if len(r.data) < 60:
        raise ServiceUnavailableError("Not enough clean candles to analyse.",
                                      code="INSUFFICIENT_DATA",
                                      details=r.quality.as_dict())
    out = summarize(r.data, is_demo=r.is_demo)
    if r.quality.status == "FAIL":
        out["abstain_reasons"].insert(0, "Market-data quality check failed for this window.")
    out["abstain_reasons"].extend(r.notes[:-1])  # e.g. futures-proxy note
    return AnalysisPayload(**r.provenance(), symbol=symbol.upper(), timeframe=timeframe, **out)
