from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.ai import registry
from app.analysis import context as C
from app.analysis import pipeline
from app.analysis import regime as R
from app.api.deps import DbDep
from app.api.v1.market import Provenance
from app.core.errors import ServiceUnavailableError
from app.market_data.factory import ProviderDep, provider_errors
from app.market_data.provider import OPTION_UNDERLYINGS

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger("app.analysis")

TF = Literal["5m", "15m", "1h", "1d"]
WINDOW = 300
MIN_CANDLES = 80


class Signal(BaseModel):
    name: str
    reading: str
    bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]


class Regime(BaseModel):
    label: str
    family: str
    adx: float
    atr_pct: float
    atr_percentile: float
    bb_width_percentile: float
    ema50_slope_pct: float
    reasons: list[str]
    method: str


class PipelineStep(BaseModel):
    key: str
    label: str
    status: Literal["DONE", "SKIPPED", "FAILED", "BLOCKED"]
    detail: str


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
    regime: Regime | None
    indicators: dict[str, Any]
    structure: dict[str, Any] | None
    patterns: list[dict[str, Any]]
    smc: dict[str, Any]
    strategies: list[dict[str, Any]]
    prediction: dict[str, Any] | None
    probabilities: dict[str, float] | None
    model_version: str | None
    setup_candidate: dict[str, Any] | None
    options: dict[str, Any] | None
    pipeline: list[PipelineStep]
    decision: Literal["NO_TRADE", "TRADE_PROPOSAL"]
    abstain_reasons: list[str]
    explanation: list[str]
    technical: dict[str, Any] | None = None
    indicator_version: str | None = None


def _candles(provider: Any, symbol: str, timeframe: str, limit: int = WINDOW) -> Any:
    with provider_errors():
        r = provider.candles(symbol, timeframe, limit)
    if len(r.data) < MIN_CANDLES:
        raise ServiceUnavailableError("Not enough clean candles to analyse.",
                                      code="INSUFFICIENT_DATA", details=r.quality.as_dict())
    return r


@router.get("/{symbol}", response_model=AnalysisPayload)
def analyse(
    provider: ProviderDep,
    db: DbDep,
    symbol: Annotated[str, Path(max_length=32)],
    timeframe: Annotated[TF, Query()] = "15m",
) -> AnalysisPayload:
    sym = symbol.upper()
    r = _candles(provider, sym, timeframe)
    mv = registry.serving(db, sym, timeframe)

    def predict_fn(ctx: C.Ctx, regime_label: str | None) -> dict[str, Any] | None:
        if mv is None:
            return None
        if r.quality.status == "FAIL":
            return {"model_version": str(mv.id), "model_name": mv.name, "probabilities": None,
                    "reason": "Data-quality check failed, so the model abstains."}
        pred = registry.predict(mv, ctx, r.source)
        registry.record(db, mv, ctx, pred, regime_label, r.source)
        return pred

    def chain_fn() -> dict[str, Any] | None:
        if sym not in OPTION_UNDERLYINGS:
            return None
        try:
            with provider_errors():
                return provider.option_chain(sym, None, 5).data
        except Exception:
            logger.warning("analysis_chain_unavailable", extra={"symbol": sym})
            return None

    out = pipeline.run(candles=r.data, timeframe=timeframe, symbol=sym,
                       quality=r.quality.as_dict(), is_demo=r.is_demo, predict_fn=predict_fn,
                       chain_fn=chain_fn)
    proxy = [n for n in r.notes if "proxy" in n]
    out["abstain_reasons"].extend(proxy)
    return AnalysisPayload(**r.provenance(), symbol=sym, timeframe=timeframe, **out)


class TimeframeView(BaseModel):
    timeframe: str
    available: bool
    reason: str | None = None
    last_close: float | None = None
    trend: str | None = None
    regime: str | None = None
    rsi: float | None = None
    supertrend: str | None = None
    adx: float | None = None
    quality: str | None = None


class MultiTimeframePayload(BaseModel):
    symbol: str
    source: str
    views: list[TimeframeView]
    alignment: str
    note: str


@router.get("/{symbol}/mtf", response_model=MultiTimeframePayload)
def multi_timeframe(provider: ProviderDep,
                    symbol: Annotated[str, Path(max_length=32)]) -> MultiTimeframePayload:
    sym = symbol.upper()
    views, source = [], "UNKNOWN"
    for tf in ("5m", "15m", "1h", "1d"):
        try:
            r = _candles(provider, sym, tf, 220)
        except Exception as exc:
            msg = getattr(exc, "message", None) or type(exc).__name__
            if getattr(exc, "status_code", None) == 404:
                raise
            views.append(TimeframeView(timeframe=tf, available=False, reason=str(msg)))
            continue
        source = r.source
        ctx = C.build(r.data, intraday=tf != "1d")
        trend, _ = pipeline._legacy_signals(ctx)
        reg = R.detect(ctx)
        st = ctx.s["supertrend_dir"][-1]
        views.append(TimeframeView(
            timeframe=tf, available=True, last_close=round(ctx.c[-1], 2), trend=trend,
            regime=reg["label"] if reg else None,
            rsi=round(ctx.s["rsi14"][-1], 1) if ctx.s["rsi14"][-1] is not None else None,
            supertrend={1: "UP", -1: "DOWN"}.get(st) if st is not None else None,
            adx=round(ctx.s["adx14"][-1], 1) if ctx.s["adx14"][-1] is not None else None,
            quality=r.quality.status))
    trends = {v.trend for v in views if v.available}
    alignment = ("ALIGNED_BULLISH" if trends == {"BULLISH"} else "ALIGNED_BEARISH"
                 if trends == {"BEARISH"} else "MIXED" if trends else "UNAVAILABLE")
    return MultiTimeframePayload(
        symbol=sym, source=source, views=views, alignment=alignment,
        note="Timeframes use the same price history, so agreement is not independent evidence.")
