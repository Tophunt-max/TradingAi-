"""Historical outcome statistics of confirmed candlestick patterns."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query

from app.analysis import patterns as PAT
from app.api.v1.market import Provenance
from app.core.errors import ServiceUnavailableError
from app.indicators.core import INDICATOR_VERSION
from app.market_data.factory import ProviderDep, provider_errors
from app.schemas.common import SYMBOL_PATTERN

router = APIRouter(prefix="/analysis", tags=["analysis"])

TF = Literal["5m", "15m", "1h", "1d"]
MIN_CANDLES = 200


class PatternStatsPayload(Provenance):
    symbol: str
    timeframe: str
    indicator_version: str
    evidence_status: Literal["NOT_EVIDENCE", "HISTORICAL_SAMPLE"]
    evidence_note: str
    outcome_definition: str
    horizon_bars: int
    cost_bps: float
    min_samples: int
    candles: int
    first_ts: Any
    last_ts: Any
    patterns: list[dict[str, Any]]
    baseline: dict[str, Any]
    limitations: list[str]


@router.get("/{symbol}/patterns/stats", response_model=PatternStatsPayload)
def pattern_stats(
    provider: ProviderDep,
    symbol: Annotated[str, Path(max_length=32, pattern=SYMBOL_PATTERN)],
    timeframe: Annotated[TF, Query()] = "15m",
    horizon: Annotated[int, Query(ge=1, le=50)] = 5,
    cost_bps: Annotated[float, Query(ge=0, le=500)] = 5.0,
    min_samples: Annotated[int, Query(ge=5, le=1000)] = PAT.MIN_SAMPLES,
    history: Annotated[int, Query(ge=MIN_CANDLES, le=3000)] = 1500,
    by_regime: Annotated[bool, Query()] = True,
) -> PatternStatsPayload:
    sym = symbol.upper()
    with provider_errors():
        r = provider.candles(sym, timeframe, history)
    if len(r.data) < MIN_CANDLES:
        raise ServiceUnavailableError("Not enough clean candles for pattern statistics.",
                                      code="INSUFFICIENT_DATA", details=r.quality.as_dict())
    stats = PAT.pattern_outcome_stats(r.data, horizon=horizon, cost_bps=cost_bps,
                                      by_regime=by_regime, min_samples=min_samples,
                                      timeframe=timeframe)
    if r.is_demo:
        status, note = "NOT_EVIDENCE", ("SIMULATED: computed on synthetic DEMO candles. These "
                                        "numbers describe the generator, not the market, and are "
                                        "not evidence for or against any pattern.")
    else:
        status, note = "HISTORICAL_SAMPLE", (
            "Historical frequencies on this window only. They are not a forecast or a "
            "recommendation, and rows flagged insufficient_sample should be ignored.")
    if r.quality.status == "FAIL":
        status = "NOT_EVIDENCE"
        note = "Market-data quality check FAILED for this window. " + note
    return PatternStatsPayload(**r.provenance(), symbol=sym, timeframe=timeframe,
                               indicator_version=INDICATOR_VERSION, evidence_status=status,
                               evidence_note=note, **stats)
