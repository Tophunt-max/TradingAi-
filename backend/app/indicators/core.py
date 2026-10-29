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
