from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.api.v1.market import Provenance
from app.core.errors import AppError
from app.indicators import core as ind
from app.market_data.factory import ProviderDep, provider_errors

router = APIRouter(prefix="/charts", tags=["charts"])

TF = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
SUPPORTED_INDICATORS = {"ema9", "ema21", "ema50", "ema200", "rsi14", "macd", "bb20", "atr14"}


class Candle(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class CandlesPayload(Provenance):
    symbol: str
    timeframe: str
    candles: list[Candle]
    indicators: dict[str, list[float | None]]
    note: str


def _round(series: list[float | None]) -> list[float | None]:
    return [round(v, 4) if v is not None else None for v in series]


def compute_indicators(rows: list[dict], wanted: set[str]) -> dict[str, list[float | None]]:
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    out: dict[str, list[float | None]] = {}
    for name in sorted(wanted):
        if name.startswith("ema"):
            out[name] = ind.ema(closes, int(name[3:]))
        elif name == "rsi14":
            out[name] = ind.rsi(closes, 14)
        elif name == "macd":
            out["macd"], out["macd_signal"], out["macd_hist"] = ind.macd(closes)
        elif name == "bb20":
            out["bb_upper"], out["bb_mid"], out["bb_lower"] = ind.bollinger(closes)
        elif name == "atr14":
            out[name] = ind.atr(highs, lows, closes, 14)
    return out


@router.get("/{symbol}/candles", response_model=CandlesPayload)
def candles(
    provider: ProviderDep,
    symbol: Annotated[str, Path(max_length=32)],
    timeframe: Annotated[TF, Query()] = "5m",
    limit: Annotated[int, Query(ge=20, le=500)] = 120,
    indicators: Annotated[str, Query(max_length=200)] = "ema9,ema21",
) -> CandlesPayload:
    wanted = {i.strip().lower() for i in indicators.split(",") if i.strip()}
    unknown = wanted - SUPPORTED_INDICATORS
    if unknown:
        raise AppError("Unsupported indicators.", code="VALIDATION_ERROR", status_code=422,
                       details=sorted(unknown))
    # Extra history so indicator warm-up does not leave the visible window empty.
    warmup = 200 if "ema200" in wanted else 60
    with provider_errors():
        r = provider.candles(symbol.upper(), timeframe, limit + warmup)
    rows = r.data
    out = compute_indicators(rows, wanted)
    return CandlesPayload(
        **r.provenance(),
        symbol=symbol.upper(),
        timeframe=timeframe,
        candles=[Candle(**c) for c in rows[-limit:]],
        indicators={k: _round(v[-limit:]) for k, v in out.items()},
        note="Only closed candles are returned; the forming candle is excluded.",
    )
