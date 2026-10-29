"""Computes every indicator series once for a candle window.

All series are causal: value[i] uses candles[0..i] only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.time import IST
from app.indicators import core as ind


@dataclass
class Ctx:
    candles: list[dict[str, Any]]
    ts: list[datetime]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741
    c: list[float]
    v: list[float]
    s: dict[str, list[Any]] = field(default_factory=dict)
    intraday: bool = True

    def __len__(self) -> int:
        return len(self.c)

    def last(self, name: str, i: int | None = None) -> Any:
        series = self.s[name]
        return series[-1 if i is None else i]


def build(candles: list[dict[str, Any]], intraday: bool = True) -> Ctx:
    o = [float(k["open"]) for k in candles]
    h = [float(k["high"]) for k in candles]
    lo = [float(k["low"]) for k in candles]
    c = [float(k["close"]) for k in candles]
    v = [float(k.get("volume") or 0) for k in candles]
    ts = [k["ts"] for k in candles]
    ctx = Ctx(candles=candles, ts=ts, o=o, h=h, l=lo, c=c, v=v, intraday=intraday)
    s = ctx.s
    for p in (5, 9, 13, 21, 50, 200):
        s[f"ema{p}"] = ind.ema(c, p)
    s["rsi14"] = ind.rsi(c, 14)
    s["macd"], s["macd_signal"], s["macd_hist"] = ind.macd(c)
    s["bb_upper"], s["bb_mid"], s["bb_lower"] = ind.bollinger(c)
    s["atr14"] = ind.atr(h, lo, c, 14)
    s["adx14"], s["pdi14"], s["mdi14"] = ind.adx(h, lo, c, 14)
    s["cci20"] = ind.cci(h, lo, c, 20)
    s["stoch_k"], s["stoch_d"] = ind.stochastic(h, lo, c)
    s["supertrend"], s["supertrend_dir"] = ind.supertrend(h, lo, c)
    s["rvol20"] = ind.relative_volume(v, 20)
    keys = [t.astimezone(IST).date() for t in ts] if intraday else list(range(len(ts)))
    has_volume = any(v)
    s["vwap"] = (ind.vwap(h, lo, c, v, keys) if intraday and has_volume
                 else [None] * len(c))
    return ctx


def daily_hlc(ctx: Ctx) -> list[tuple[Any, float, float, float]]:
    """(IST date, high, low, close) per session, in order."""
    out: list[tuple[Any, float, float, float]] = []
    for t, h, lo, c in zip(ctx.ts, ctx.h, ctx.l, ctx.c, strict=True):
        d = t.astimezone(IST).date()
        if out and out[-1][0] == d:
            _, ph, pl, _ = out[-1]
            out[-1] = (d, max(ph, h), min(pl, lo), c)
        else:
            out.append((d, h, lo, c))
    return out
