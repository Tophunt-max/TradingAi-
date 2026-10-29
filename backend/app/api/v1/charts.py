from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.api.deps import AuthDep, SettingsDep
from app.api.v1.market import Provenance, demo_provenance, require_demo
from app.core.errors import AppError, NotFoundError
from app.core.time import utcnow
from app.indicators import core as ind
from app.market_data import demo

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


@router.get("/{symbol}/candles", response_model=CandlesPayload)
def candles(
    _: AuthDep,
    settings: SettingsDep,
    symbol: Annotated[str, Path(max_length=32)],
    timeframe: Annotated[TF, Query()] = "5m",
    limit: Annotated[int, Query(ge=20, le=500)] = 120,
    indicators: Annotated[str, Query(max_length=200)] = "ema9,ema21",
) -> CandlesPayload:
    require_demo(settings)
    sym = symbol.upper()
    if sym not in demo.INSTRUMENTS:
        raise NotFoundError(f"Unknown instrument '{symbol}'.")
    now = utcnow()
    wanted = {i.strip().lower() for i in indicators.split(",") if i.strip()}
    unknown = wanted - SUPPORTED_INDICATORS
    if unknown:
        raise AppError("Unsupported indicators.", code="VALIDATION_ERROR", status_code=422,
                       details=sorted(unknown))
    # Fetch extra history so indicator warm-up does not leave the visible window empty.
    warmup = 200 if "ema200" in wanted else 60
    rows = demo.candles(sym, timeframe, now, limit + warmup)
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
            line, sig, hist = ind.macd(closes)
            out["macd"], out["macd_signal"], out["macd_hist"] = line, sig, hist
        elif name == "bb20":
            out["bb_upper"], out["bb_mid"], out["bb_lower"] = ind.bollinger(closes)
        elif name == "atr14":
            out[name] = ind.atr(highs, lows, closes, 14)
    return CandlesPayload(
        **demo_provenance(now),
        symbol=sym,
        timeframe=timeframe,
        candles=[Candle(**r) for r in rows[-limit:]],
        indicators={k: _round(v[-limit:]) for k, v in out.items()},
        note="Only closed candles are returned; the forming candle is excluded.",
    )
