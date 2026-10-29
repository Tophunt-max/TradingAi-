from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.analysis.technical_summary import summarize
from app.api.deps import AuthDep, SettingsDep
from app.api.v1.market import Provenance, demo_provenance, require_demo
from app.core.errors import NotFoundError
from app.core.time import utcnow
from app.market_data import demo

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
    _: AuthDep,
    settings: SettingsDep,
    symbol: Annotated[str, Path(max_length=32)],
    timeframe: Annotated[Literal["5m", "15m", "1h", "1d"], Query()] = "15m",
) -> AnalysisPayload:
    require_demo(settings)
    sym = symbol.upper()
    if sym not in demo.INSTRUMENTS:
        raise NotFoundError(f"Unknown instrument '{symbol}'.")
    now = utcnow()
    rows = demo.candles(sym, timeframe, now, 150)
    return AnalysisPayload(**demo_provenance(now), symbol=sym, timeframe=timeframe,
                           **summarize(rows, is_demo=True))
