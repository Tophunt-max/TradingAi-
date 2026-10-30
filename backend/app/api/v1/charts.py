from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from app.analysis import context as C
from app.analysis import patterns as PAT
from app.analysis import smc as SMC
from app.api.v1.market import Provenance
from app.core.errors import AppError
from app.indicators import core as ind
from app.market_data.factory import ProviderDep, provider_errors
from app.schemas.common import SYMBOL_PATTERN

router = APIRouter(prefix="/charts", tags=["charts"])

TF = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
# Requested indicator -> series keys in the analysis context.
INDICATOR_SERIES = {
    "ema9": ["ema9"], "ema21": ["ema21"], "ema50": ["ema50"], "ema200": ["ema200"],
    "rsi14": ["rsi14"], "macd": ["macd", "macd_signal", "macd_hist"],
    "bb20": ["bb_upper", "bb_mid", "bb_lower"], "atr14": ["atr14"], "vwap": ["vwap"],
    "supertrend": ["supertrend", "supertrend_dir"], "adx14": ["adx14", "pdi14", "mdi14"],
    "cci20": ["cci20"], "stoch": ["stoch_k", "stoch_d"], "rvol20": ["rvol20"],
    # ta-3
    "ema100": ["ema100"], "sma20": ["sma20"], "roc12": ["roc12"], "mom10": ["mom10"],
    "obv": ["obv"], "volma20": ["volma20"], "hv20": ["hv20"], "rv20": ["rv20"],
    "pkv20": ["pkv20"], "volstate": ["vol_score"],
    # Cloud as drawn at each candle (computed 26 bars earlier). No chikou: it would
    # show future closes on past candles.
    "ichimoku": ["ichi_tenkan", "ichi_kijun", "ichi_span_a", "ichi_span_b"],
}
# Computed on request: anchored VWAP from the latest confirmed swing pivot.
SPECIAL_INDICATORS = {"avwap"}
SUPPORTED_INDICATORS = set(INDICATOR_SERIES) | SPECIAL_INDICATORS
# Configurable periods in the same style, e.g. sma50, ema34, roc5, hv10.
DYNAMIC = re.compile(r"^(sma|ema|roc|hv)(\d{1,3})$")
MIN_PERIOD, MAX_PERIOD = 2, 250
WARMUP = {"ema200": 200, "ema100": 120, "ichimoku": 80}


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


def _dynamic(name: str) -> tuple[str, int] | None:
    m = DYNAMIC.match(name)
    if not m:
        return None
    period = int(m.group(2))
    return (m.group(1), period) if MIN_PERIOD <= period <= MAX_PERIOD else None


def unsupported(wanted: set[str]) -> set[str]:
    return {w for w in wanted if w not in SUPPORTED_INDICATORS and _dynamic(w) is None}


def warmup_bars(wanted: set[str]) -> int:
    need = [WARMUP.get(w, 60) for w in wanted] + [60]
    for w in wanted:
        d = _dynamic(w)
        if d:
            need.append(d[1] + 20)
    return max(need)


def anchored_vwap_series(ctx: C.Ctx) -> tuple[list[Any], dict[str, Any] | None]:
    """AVWAP anchored at the most recent confirmed swing (high or low).

    Values start at the swing's CONFIRMATION bar, never earlier, because the
    anchor could not have been chosen before the swing was confirmed.
    """
    ph, pl = PAT.pivots(ctx)
    swings = sorted(ph + pl)
    if not swings or not any(ctx.v):
        return [None] * len(ctx), None
    i, price = swings[-1]
    out = ind.anchored_vwap(ctx.h, ctx.l, ctx.c, ctx.v, i, start=i + PAT.PIVOT_R)
    return out, {"anchor_ts": ctx.ts[i], "anchor_price": price,
                 "visible_from_ts": ctx.ts[i + PAT.PIVOT_R]}


def compute_indicators(ctx: C.Ctx, wanted: set[str]) -> dict[str, list[Any]]:
    out = {key: ctx.s[key] for name in sorted(wanted) if name in INDICATOR_SERIES
           for key in INDICATOR_SERIES[name]}
    for name in sorted(wanted):
        d = _dynamic(name)
        if name in INDICATOR_SERIES or d is None:
            continue
        kind, p = d
        if kind == "sma":
            out[name] = ind.sma(ctx.c, p)
        elif kind == "ema":
            out[name] = ind.ema(ctx.c, p)
        elif kind == "roc":
            out[name] = ind.roc(ctx.c, p)
        else:
            out[name] = ind.historical_volatility(
                ctx.c, p, ind.periods_per_year(ctx.timeframe if ctx.intraday else None))
    if "avwap" in wanted:
        out["avwap"] = anchored_vwap_series(ctx)[0]
    return out


def annotations(ctx: C.Ctx, offset: int,
                quality: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Confirmed patterns and SMC zones that fall inside the visible window."""
    n = len(ctx)

    def vis(e: dict[str, Any]) -> bool:
        return e["confirmed_index"] >= offset

    def clean(e: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in e.items() if not k.endswith("index")}

    smc = SMC.analyse(ctx, quality=quality)
    zones = []
    for z in smc["fair_value_gaps"] + smc["order_blocks"] + smc["supply_demand"]:
        end = z["mitigated_index"] if z["mitigated"] else n - 1
        if end < offset:
            continue
        zones.append({**clean(z), "start_ts": ctx.ts[max(z["index"], offset)],
                      "end_ts": ctx.ts[end]})
    return {
        "patterns": [clean(e) for e in PAT.all_patterns(ctx) if vis(e)][-40:],
        "structure": sorted([clean(e) for e in smc["structure_events"] + smc["liquidity_sweeps"]
                             + smc["breakout_retests"] + smc["failed_breakouts"] if vis(e)],
                            key=lambda e: e["confirmed_ts"])[-30:],
        "zones": zones[-20:],
    }


@router.get("/{symbol}/candles", response_model=CandlesPayload)
def candles(
    provider: ProviderDep,
    symbol: Annotated[str, Path(max_length=32, pattern=SYMBOL_PATTERN)],
    timeframe: Annotated[TF, Query()] = "5m",
    limit: Annotated[int, Query(ge=20, le=500)] = 120,
    indicators: Annotated[str, Query(max_length=200)] = "ema9,ema21",
    include_annotations: Annotated[bool, Query(alias="annotations")] = False,
) -> CandlesPayload:
    wanted = {i.strip().lower() for i in indicators.split(",") if i.strip()}
    unknown = unsupported(wanted)
    if unknown:
        raise AppError("Unsupported indicators.", code="VALIDATION_ERROR", status_code=422,
                       details=sorted(unknown))
    # Extra history so indicator warm-up does not leave the visible window empty.
    warmup = warmup_bars(wanted)
    with provider_errors():
        r = provider.candles(symbol.upper(), timeframe, limit + warmup)
    rows = r.data
    ctx = C.build(rows, intraday=timeframe != "1d", timeframe=timeframe)
    out = compute_indicators(ctx, wanted)
    offset = max(len(rows) - limit, 0)
    return CandlesPayload(
        **r.provenance(),
        symbol=symbol.upper(),
        timeframe=timeframe,
        candles=[Candle(**c) for c in rows[-limit:]],
        indicators={k: _round(v[-limit:]) for k, v in out.items()},
        annotations=annotations(ctx, offset, r.quality.status) if include_annotations else None,
        note="Only closed candles are returned; the forming candle is excluded.",
    )
