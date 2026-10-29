"""Causal technical indicators.

Every output at index i depends only on inputs[0..i], so values never change
when later candles arrive (no lookahead, no repainting). Warm-up positions are
None rather than a guessed value.
"""

from __future__ import annotations

from collections.abc import Sequence

Series = list[float | None]


def sma(values: Sequence[float], period: int) -> Series:
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
    """EMA seeded with the SMA of the first `period` values."""
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
