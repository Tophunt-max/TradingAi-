from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.analysis import context as C
from app.analysis import patterns as PAT
from app.analysis import smc as SMC
from app.api.v1.market import Provenance
from app.core.errors import AppError
from app.market_data.factory import ProviderDep, provider_errors

router = APIRouter(prefix="/charts", tags=["charts"])

TF = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
# Requested indicator -> series keys in the analysis context.
INDICATOR_SERIES = {
    "ema9": ["ema9"], "ema21": ["ema21"], "ema50": ["ema50"], "ema200": ["ema200"],
    "rsi14": ["rsi14"], "macd": ["macd", "macd_signal", "macd_hist"],
    "bb20": ["bb_upper", "bb_mid", "bb_lower"], "atr14": ["atr14"], "vwap": ["vwap"],
    "supertrend": ["supertrend", "supertrend_dir"], "adx14": ["adx14", "pdi14", "mdi14"],
    "cci20": ["cci20"], "stoch": ["stoch_k", "stoch_d"], "rvol20": ["rvol20"],
}
SUPPORTED_INDICATORS = set(INDICATOR_SERIES)


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
    annotations: dict[str, list[dict[str, Any]]] | None = None
    note: str


def _round(series: list[Any]) -> list[float | None]:
    return [round(float(v), 4) if v is not None else None for v in series]


def compute_indicators(ctx: C.Ctx, wanted: set[str]) -> dict[str, list[Any]]:
    return {key: ctx.s[key] for name in sorted(wanted) for key in INDICATOR_SERIES[name]}


def annotations(ctx: C.Ctx, offset: int) -> dict[str, list[dict[str, Any]]]:
    """Confirmed patterns and SMC zones that fall inside the visible window."""
    n = len(ctx)

    def vis(e: dict[str, Any]) -> bool:
        return e["confirmed_index"] >= offset

    def clean(e: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in e.items() if not k.endswith("index")}

    smc = SMC.analyse(ctx)
    zones = []
    for z in smc["fair_value_gaps"] + smc["order_blocks"]:
        end = z["mitigated_index"] if z["mitigated"] else n - 1
        if end < offset:
            continue
        zones.append({**clean(z), "start_ts": ctx.ts[max(z["index"], offset)],
                      "end_ts": ctx.ts[end]})
    return {
        "patterns": [clean(e) for e in PAT.all_patterns(ctx) if vis(e)][-40:],
        "structure": [clean(e) for e in smc["structure_events"] + smc["liquidity_sweeps"]
                      if vis(e)][-30:],
        "zones": zones[-20:],
    }


@router.get("/{symbol}/candles", response_model=CandlesPayload)
def candles(
    provider: ProviderDep,
    symbol: Annotated[str, Path(max_length=32)],
    timeframe: Annotated[TF, Query()] = "5m",
    limit: Annotated[int, Query(ge=20, le=500)] = 120,
    indicators: Annotated[str, Query(max_length=200)] = "ema9,ema21",
    include_annotations: Annotated[bool, Query(alias="annotations")] = False,
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
    ctx = C.build(rows, intraday=timeframe != "1d")
    out = compute_indicators(ctx, wanted)
    offset = max(len(rows) - limit, 0)
    return CandlesPayload(
        **r.provenance(),
        symbol=symbol.upper(),
        timeframe=timeframe,
        candles=[Candle(**c) for c in rows[-limit:]],
        indicators={k: _round(v[-limit:]) for k, v in out.items()},
        annotations=annotations(ctx, offset) if include_annotations else None,
        note="Only closed candles are returned; the forming candle is excluded.",
    )
