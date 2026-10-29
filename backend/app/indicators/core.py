"""Causal technical indicators.

Every output at index i depends only on inputs[0..i], so values never change
when later candles arrive (no lookahead, no repainting). Warm-up positions are
None rather than a guessed value.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Series = list[float | None]

# Bump whenever an indicator definition, default period or warm-up rule changes.
INDICATOR_VERSION = "ta-3"


def _ok(v: float | None) -> bool:
    """True for a usable number (not None, not NaN, not inf)."""
    return v is not None and math.isfinite(v)


def _has_missing(values: Sequence[float | None]) -> bool:
    return any(not _ok(v) for v in values)


def sma(values: Sequence[float], period: int) -> Series:
    """Simple moving average. A window that contains a missing value is None."""
    if _has_missing(values):
        return sma_opt([v if _ok(v) else None for v in values], period)
    out: Series = [None] * len(values)
    window = 0.0
    for i, v in enumerate(values):
        window += v
        if i >= period:
            window -= values[i - period]
        if i >= period - 1:
            out[i] = window / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """EMA seeded with the SMA of the first `period` values.

    With missing values the EMA restarts after the gap and needs `period`
    fresh values before it is reported again.
    """
    if _has_missing(values):
        return _ema_gaps(values, period)
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _ema_gaps(values: Sequence[float | None], period: int) -> Series:
    out: Series = [None] * len(values)
    k = 2 / (period + 1)
    seed: list[float] = []
    prev: float | None = None
    for i, v in enumerate(values):
        if not _ok(v):
            seed, prev = [], None
            continue
        assert v is not None
        if prev is None:
            seed.append(v)
            if len(seed) == period:
                prev = sum(seed) / period
                out[i] = prev
            continue
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Series:
    """Wilder's RSI."""
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period

    def value(g: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / loss)

    out[period] = value(avg_g, avg_l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        out[i] = value(avg_g, avg_l)
    return out


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Series, Series, Series]:
    f, s = ema(closes, fast), ema(closes, slow)
    line: Series = [
        (a - b) if a is not None and b is not None else None for a, b in zip(f, s, strict=True)
    ]
    first = next((i for i, v in enumerate(line) if v is not None), None)
    sig: Series = [None] * len(closes)
    if first is not None:
        tail = ema([v for v in line[first:] if v is not None], signal)
        sig[first:] = tail
    hist: Series = [
        (a - b) if a is not None and b is not None else None for a, b in zip(line, sig, strict=True)
    ]
    return line, sig, hist


def bollinger(
    closes: Sequence[float], period: int = 20, k: float = 2.0
) -> tuple[Series, Series, Series]:
    mid = sma(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        m = mid[i]
        assert m is not None
        sd = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        upper[i], lower[i] = m + k * sd, m - k * sd
    return upper, mid, lower


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> Series:
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    out: Series = [None] * len(closes)
    if len(trs) < period:
        return out
    prev = sum(trs[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def confirmed_pivots(
    highs: Sequence[float], lows: Sequence[float], left: int = 3, right: int = 3
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Swing highs / lows that are confirmed by `right` later candles.

    A pivot at index i is only reported once candle i + right exists, so the
    most recent `right` candles can never be pivots (no repainting).
    """
    ph, pl = [], []
    for i in range(left, len(highs) - right):
        h = highs[i]
        if all(h > highs[j] for j in range(i - left, i)) and all(
            h >= highs[j] for j in range(i + 1, i + right + 1)
        ):
            ph.append((i, h))
        lo = lows[i]
        if all(lo < lows[j] for j in range(i - left, i)) and all(
            lo <= lows[j] for j in range(i + 1, i + right + 1)
        ):
            pl.append((i, lo))
    return ph, pl


# ---------------------------------------------------------------- Phase 4


def sma_opt(values: Sequence[float | None], period: int) -> Series:
    """SMA over a series that may start with None (warm-up)."""
    out: Series = [None] * len(values)
    window: list[float] = []
    for i, v in enumerate(values):
        if v is None:
            window = []
            continue
        window.append(v)
        if len(window) > period:
            window.pop(0)
        if len(window) == period:
            out[i] = sum(window) / period
    return out


def vwap(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
         volumes: Sequence[float], session_keys: Sequence[object]) -> Series:
    """Session VWAP; resets whenever the session key (IST date) changes."""
    out: Series = []
    cum_pv = cum_v = 0.0
    prev: object = object()
    for h, lo, c, v, k in zip(highs, lows, closes, volumes, session_keys, strict=True):
        if k != prev:
            cum_pv = cum_v = 0.0
            prev = k
        cum_pv += (h + lo + c) / 3 * v
        cum_v += v
        out.append(cum_pv / cum_v if cum_v > 0 else None)
    return out


def supertrend(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               period: int = 10, mult: float = 3.0) -> tuple[Series, list[int | None]]:
    """Supertrend line and direction (+1 up, -1 down)."""
    a = atr(highs, lows, closes, period)
    n = len(closes)
    line: Series = [None] * n
    direction: list[int | None] = [None] * n
    fu = fl = None
    trend = 1
    for i in range(n):
        if a[i] is None:
            continue
        hl2 = (highs[i] + lows[i]) / 2
        bu, bl = hl2 + mult * a[i], hl2 - mult * a[i]
        if fu is None or fl is None:
            fu, fl = bu, bl
        else:
            fu = bu if bu < fu or closes[i - 1] > fu else fu
            fl = bl if bl > fl or closes[i - 1] < fl else fl
            if trend == 1 and closes[i] < fl:
                trend = -1
            elif trend == -1 and closes[i] > fu:
                trend = 1
        line[i] = fl if trend == 1 else fu
        direction[i] = trend
    return line, direction


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> tuple[Series, Series, Series]:
    """Wilder's ADX, +DI and -DI."""
    n = len(closes)
    adx_s: Series = [None] * n
    pdi_s: Series = [None] * n
    mdi_s: Series = [None] * n
    if n < 2 * period + 1:
        return adx_s, pdi_s, mdi_s
    tr, pdm, mdm = [0.0], [0.0], [0.0]
    for i in range(1, n):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    s_tr, s_p, s_m = sum(tr[1:period + 1]), sum(pdm[1:period + 1]), sum(mdm[1:period + 1])
    dxs: list[tuple[int, float]] = []
    for i in range(period, n):
        if i > period:
            s_tr += tr[i] - s_tr / period
            s_p += pdm[i] - s_p / period
            s_m += mdm[i] - s_m / period
        pdi = 100 * s_p / s_tr if s_tr else 0.0
        mdi = 100 * s_m / s_tr if s_tr else 0.0
        pdi_s[i], mdi_s[i] = pdi, mdi
        dxs.append((i, 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0))
    first = dxs[:period]
    val = sum(d for _, d in first) / period
    adx_s[first[-1][0]] = val
    for i, d in dxs[period:]:
        val = (val * (period - 1) + d) / period
        adx_s[i] = val
    return adx_s, pdi_s, mdi_s


def cci(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 20) -> Series:
    tp = [(h + lo + c) / 3 for h, lo, c in zip(highs, lows, closes, strict=True)]
    out: Series = [None] * len(tp)
    for i in range(period - 1, len(tp)):
        w = tp[i - period + 1:i + 1]
        m = sum(w) / period
        md = sum(abs(x - m) for x in w) / period
        out[i] = (tp[i] - m) / (0.015 * md) if md else 0.0
    return out


def stochastic(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               k_period: int = 14, k_smooth: int = 3, d_period: int = 3) -> tuple[Series, Series]:
    raw: Series = [None] * len(closes)
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        raw[i] = 100 * (closes[i] - ll) / (hh - ll) if hh > ll else 50.0
    k = sma_opt(raw, k_smooth)
    return k, sma_opt(k, d_period)


def relative_volume(volumes: Sequence[float], period: int = 20) -> Series:
    """Volume divided by the mean of the PREVIOUS `period` bars."""
    out: Series = [None] * len(volumes)
    for i in range(period, len(volumes)):
        base = sum(volumes[i - period:i]) / period
        out[i] = volumes[i] / base if base > 0 else None
    return out


def classic_pivots(high: float, low: float, close: float) -> dict[str, float]:
    """Floor-trader pivots from the previous completed session."""
    p = (high + low + close) / 3
    return {"P": p, "R1": 2 * p - low, "S1": 2 * p - high, "R2": p + (high - low),
            "S2": p - (high - low), "R3": high + 2 * (p - low), "S3": low - 2 * (high - p)}


FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def fibonacci(swing_high: float, swing_low: float, up_move: bool) -> dict[str, float]:
    """Retracement levels of the move from swing_low to swing_high (or reverse)."""
    rng = swing_high - swing_low
    if up_move:
        return {f"{r:.3f}": swing_high - r * rng for r in FIB_RATIOS}
    return {f"{r:.3f}": swing_low + r * rng for r in FIB_RATIOS}


def candle_anatomy(o: float, h: float, lo: float, c: float) -> dict[str, float | str]:
    rng = h - lo
    body = abs(c - o)
    return {
        "direction": "BULLISH" if c > o else "BEARISH" if c < o else "NEUTRAL",
        "range": rng,
        "body": body,
        "upper_wick": h - max(o, c),
        "lower_wick": min(o, c) - lo,
        "body_pct": body / rng if rng else 0.0,
    }


# ------------------------------------------------------------------ ta-3
# Every function below is causal: out[i] uses inputs[0..i] only. Series
# inputs may contain None/NaN (missing data); any window that touches a
# missing value is None rather than a partial value.

FIB_EXT_RATIOS = (0.618, 1.0, 1.272, 1.618, 2.0, 2.618)
TRADING_DAYS = 252
SESSION_MINUTES = 375  # NSE cash session 09:15-15:30 IST
TIMEFRAME_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def periods_per_year(timeframe: str | None) -> float:
    """Bars per year used to annualise volatility (252 sessions of 375 minutes)."""
    minutes = TIMEFRAME_MINUTES.get(timeframe or "")
    return TRADING_DAYS * SESSION_MINUTES / minutes if minutes else float(TRADING_DAYS)


def roc(values: Sequence[float | None], period: int = 12) -> Series:
    """Rate of change in percent: 100 × (v[i] / v[i-period] − 1)."""
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        a, b = values[i], values[i - period]
        if _ok(a) and _ok(b) and b:
            out[i] = 100 * (a / b - 1)  # type: ignore[operator]
    return out


def momentum(values: Sequence[float | None], period: int = 10) -> Series:
    """Price momentum: v[i] − v[i-period]."""
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        a, b = values[i], values[i - period]
        if _ok(a) and _ok(b):
            out[i] = a - b  # type: ignore[operator]
    return out


def obv(closes: Sequence[float | None], volumes: Sequence[float | None]) -> Series:
    """On-balance volume. A missing close or volume makes that bar None and
    restarts the running total at 0 (the level before a gap is not comparable)."""
    out: Series = [None] * len(closes)
    total: float | None = None
    prev_c: float | None = None
    for i, (c, v) in enumerate(zip(closes, volumes, strict=True)):
        if not (_ok(c) and _ok(v)):
            total, prev_c = None, None
            continue
        assert c is not None and v is not None
        if total is None or prev_c is None:
            total = 0.0
        elif c > prev_c:
            total += v
        elif c < prev_c:
            total -= v
        out[i], prev_c = total, c
    return out


def volume_ma(volumes: Sequence[float | None], period: int = 20) -> Series:
    """Simple moving average of volume (includes the current bar)."""
    return sma(volumes, period)  # type: ignore[arg-type]


def _log_returns(closes: Sequence[float | None]) -> Series:
    out: Series = [None] * len(closes)
    for i in range(1, len(closes)):
        a, b = closes[i], closes[i - 1]
        if _ok(a) and _ok(b) and a > 0 and b > 0:  # type: ignore[operator]
            out[i] = math.log(a / b)  # type: ignore[operator]
    return out


def historical_volatility(closes: Sequence[float | None], period: int = 20,
                          periods_per_year: float = TRADING_DAYS) -> Series:
    """Close-to-close volatility: sample st. dev. (ddof = 1) of the last `period`
    log returns × sqrt(periods_per_year). First value at index `period`."""
    r = _log_returns(closes)
    out: Series = [None] * len(closes)
    for i in range(period, len(closes)):
        w = r[i - period + 1:i + 1]
        if any(x is None for x in w):
            continue
        m = sum(w) / period  # type: ignore[arg-type]
        var = sum((x - m) ** 2 for x in w) / (period - 1)  # type: ignore[operator]
        out[i] = math.sqrt(var * periods_per_year)
    return out


def realized_volatility(closes: Sequence[float | None], period: int = 20,
                        periods_per_year: float | None = None) -> Series:
    """Realized volatility sqrt(Σ r²) over the last `period` log returns.
    With `periods_per_year` it is annualised by sqrt(periods_per_year / period)."""
    r = _log_returns(closes)
    out: Series = [None] * len(closes)
    scale = math.sqrt(periods_per_year / period) if periods_per_year else 1.0
    for i in range(period, len(closes)):
        w = r[i - period + 1:i + 1]
        if any(x is None for x in w):
            continue
        out[i] = math.sqrt(sum(x * x for x in w)) * scale  # type: ignore[operator]
    return out


def parkinson_volatility(highs: Sequence[float], lows: Sequence[float], period: int = 20,
                         periods_per_year: float = TRADING_DAYS) -> Series:
    """Parkinson high-low range volatility, annualised."""
    out: Series = [None] * len(highs)
    k = 1 / (4 * math.log(2) * period)
    sq: list[float | None] = [math.log(h / lo) ** 2 if _ok(h) and _ok(lo) and lo > 0 and h >= lo
                              else None for h, lo in zip(highs, lows, strict=True)]
    for i in range(period - 1, len(highs)):
        w = sq[i - period + 1:i + 1]
        if any(x is None for x in w):
            continue
        out[i] = math.sqrt(k * sum(w) * periods_per_year)  # type: ignore[arg-type]
    return out


def rolling_percentile(values: Sequence[float | None], lookback: int = 100,
                       min_periods: int = 20) -> Series:
    """Mid-rank percentile of the current value among the valid values of the
    last `lookback` bars (inclusive): (count below + ½ count equal) / count.
    None until `min_periods` valid values exist."""
    out: Series = [None] * len(values)
    for i, v in enumerate(values):
        if not _ok(v):
            continue
        w = [x for x in values[max(0, i - lookback + 1):i + 1] if _ok(x)]
        if len(w) >= min_periods:
            below = sum(1 for x in w if x < v)  # type: ignore[operator]
            out[i] = (below + 0.5 * sum(1 for x in w if x == v)) / len(w)
    return out


def volatility_state(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                     lookback: int = 100, low: float = 0.2, high: float = 0.8,
                     atr_period: int = 14, bb_period: int = 20
                     ) -> tuple[Series, list[str | None]]:
    """Volatility contraction / expansion flag.

    Score = mean of the rolling percentiles of ATR% and Bollinger width over
    `lookback` bars. CONTRACTION if score ≤ low, EXPANSION if ≥ high, else NORMAL.
    """
    a = atr(highs, lows, closes, atr_period)
    up, mid, lo = bollinger(closes, bb_period)
    atr_pct = [x / c if x is not None and c else None for x, c in zip(a, closes, strict=True)]
    width = [(u - b) / m if u is not None and b is not None and m else None
             for u, b, m in zip(up, lo, mid, strict=True)]
    pa, pw = rolling_percentile(atr_pct, lookback), rolling_percentile(width, lookback)
    score: Series = [(x + y) / 2 if x is not None and y is not None else None
                     for x, y in zip(pa, pw, strict=True)]
    state: list[str | None] = [None if s is None else "CONTRACTION" if s <= low
                               else "EXPANSION" if s >= high else "NORMAL" for s in score]
    return score, state


def _midpoint(highs: Sequence[float], lows: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(highs)
    for i in range(period - 1, len(highs)):
        out[i] = (max(highs[i - period + 1:i + 1]) + min(lows[i - period + 1:i + 1])) / 2
    return out


def ichimoku(highs: Sequence[float], lows: Sequence[float], tenkan: int = 9, kijun: int = 26,
             senkou_b: int = 52, displacement: int = 26) -> dict[str, Series]:
    """Ichimoku lines.

    - tenkan, kijun: midpoints of the last 9 / 26 bars.
    - senkou_a / senkou_b: the span values COMPUTED at bar i ((tenkan+kijun)/2 and
      the 52-bar midpoint). Charts plot them `displacement` bars ahead.
    - span_a / span_b: the cloud as DRAWN at bar i, i.e. the value computed at
      i − displacement. Both are known at i, so either is safe for features.
    - Chikou (close plotted 26 bars back) is deliberately not produced: at bar
      j it would show close[j + 26], a future value.
    """
    t, k = _midpoint(highs, lows, tenkan), _midpoint(highs, lows, kijun)
    a: Series = [(x + y) / 2 if x is not None and y is not None else None
                 for x, y in zip(t, k, strict=True)]
    b = _midpoint(highs, lows, senkou_b)
    n = len(highs)
    span_a: Series = [a[i - displacement] if i >= displacement else None for i in range(n)]
    span_b: Series = [b[i - displacement] if i >= displacement else None for i in range(n)]
    return {"tenkan": t, "kijun": k, "senkou_a": a, "senkou_b": b,
            "span_a": span_a, "span_b": span_b}


def anchored_vwap(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                  volumes: Sequence[float], anchor: int, start: int | None = None) -> Series:
    """VWAP of typical price from candle `anchor` onward (None before it).

    `start` (≥ anchor) hides values before the bar at which the anchor became
    known, e.g. a swing pivot's confirmation bar, so the line is never shown at
    a time when the anchor could not have been chosen.
    """
    out: Series = [None] * len(closes)
    if anchor < 0 or anchor >= len(closes):
        return out
    show_from = max(anchor, start if start is not None else anchor)
    pv = vol = 0.0
    for i in range(anchor, len(closes)):
        v = volumes[i] or 0.0
        pv += (highs[i] + lows[i] + closes[i]) / 3 * v
        vol += v
        if i >= show_from and vol > 0:
            out[i] = pv / vol
    return out


def directional_movement(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                         period: int = 14) -> tuple[Series, Series, Series]:
    """(+DI, −DI, ADX), Wilder smoothing. Same numbers as `adx`, reordered."""
    a, p, m = adx(highs, lows, closes, period)
    return p, m, a


def fibonacci_extension(swing_high: float, swing_low: float, up_move: bool,
                        retrace: float | None = None,
                        ratios: Sequence[float] = FIB_EXT_RATIOS) -> dict[str, float]:
    """Extension levels of the move swing_low → swing_high (or reverse).

    Without `retrace` the levels project from the move's origin
    (origin ± r × range). With a retracement point C they are the classic
    A-B-C projection C ± r × range.
    """
    rng = swing_high - swing_low
    if up_move:
        base = swing_low if retrace is None else retrace
        return {f"{r:.3f}": base + r * rng for r in ratios}
    base = swing_high if retrace is None else retrace
    return {f"{r:.3f}": base - r * rng for r in ratios}
