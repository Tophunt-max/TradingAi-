"""Synthetic DEMO market data.

Prices come from a deterministic function of time (multi-scale sinusoids plus
seeded noise), so quotes and candles agree with each other and are reproducible.
They are NOT real prices, must never feed trading decisions, and every API
payload built from them is labelled `source="DEMO"` / `is_demo=True`.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

DEMO_DISCLAIMER = "DEMO data: synthetic values for UI testing only. Not real market prices."


@dataclass(frozen=True)
class DemoInstrument:
    symbol: str
    name: str
    exchange: str
    segment: str  # INDEX | EQ
    base: float
    vol: float  # rough daily % volatility
    sector: str | None = None
    has_options: bool = False


INSTRUMENTS: dict[str, DemoInstrument] = {
    i.symbol: i
    for i in (
        DemoInstrument("NIFTY", "NIFTY 50 (demo)", "NSE", "INDEX", 24000, 0.8, has_options=True),
        DemoInstrument("BANKNIFTY", "NIFTY BANK (demo)", "NSE", "INDEX", 52000, 1.1,
                       has_options=True),
        DemoInstrument("FINNIFTY", "NIFTY FIN SERVICE (demo)", "NSE", "INDEX", 23500, 0.9),
        DemoInstrument("SENSEX", "S&P BSE SENSEX (demo)", "BSE", "INDEX", 79000, 0.8),
        DemoInstrument("RELIANCE", "Reliance Industries (demo)", "NSE", "EQ", 2850, 1.4, "Energy"),
        DemoInstrument("TCS", "Tata Consultancy Services (demo)", "NSE", "EQ", 4000, 1.2, "IT"),
        DemoInstrument("INFY", "Infosys (demo)", "NSE", "EQ", 1450, 1.5, "IT"),
        DemoInstrument("HDFCBANK", "HDFC Bank (demo)", "NSE", "EQ", 1680, 1.2, "Banking"),
        DemoInstrument("ICICIBANK", "ICICI Bank (demo)", "NSE", "EQ", 1150, 1.3, "Banking"),
        DemoInstrument("SBIN", "State Bank of India (demo)", "NSE", "EQ", 820, 1.6, "Banking"),
        DemoInstrument("ITC", "ITC (demo)", "NSE", "EQ", 470, 1.1, "FMCG"),
        DemoInstrument("LT", "Larsen & Toubro (demo)", "NSE", "EQ", 3600, 1.3, "Infra"),
        DemoInstrument("BHARTIARTL", "Bharti Airtel (demo)", "NSE", "EQ", 1550, 1.3, "Telecom"),
        DemoInstrument("TATAMOTORS", "Tata Motors (demo)", "NSE", "EQ", 950, 2.0, "Auto"),
    )
}

TIMEFRAMES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}

# (period in minutes, amplitude as a multiple of daily vol)
_WAVES = ((47, 0.08), (283, 0.25), (1913, 0.6), (12_007, 1.6), (61_001, 3.2))


def _noise(key: str) -> float:
    """Deterministic value in [-1, 1) for a key."""
    h = hashlib.sha256(key.encode()).digest()
    return (int.from_bytes(h[:8], "big") / 2**64) * 2 - 1


def _phases(symbol: str) -> list[float]:
    return [_noise(f"{symbol}:phase:{k}") * math.pi for k in range(len(_WAVES))]


def price_at(symbol: str, t: datetime) -> float:
    inst = INSTRUMENTS[symbol]
    minute = int(t.timestamp() // 60)
    phases = _phases(symbol)
    x = sum(
        (amp * inst.vol / 100) * math.sin(2 * math.pi * minute / period + ph)
        for (period, amp), ph in zip(_WAVES, phases, strict=True)
    )
    x += _noise(f"{symbol}:{minute}") * inst.vol / 100 * 0.03
    return inst.base * math.exp(x)


def q2(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def quote(symbol: str, now: datetime) -> dict[str, object]:
    inst = INSTRUMENTS[symbol]
    now = _floor_minute(now)
    last = price_at(symbol, now)
    prev_close = price_at(symbol, now - timedelta(days=1))
    day_samples = [price_at(symbol, now - timedelta(minutes=m)) for m in range(0, 375, 15)]
    change = last - prev_close
    volume = int(abs(_noise(f"{symbol}:vol:{now.date()}")) * 9e6 + 1e6)
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "exchange": inst.exchange,
        "segment": inst.segment,
        "sector": inst.sector,
        "last": q2(last),
        "prev_close": q2(prev_close),
        "change": q2(change),
        "change_pct": q2(change / prev_close * 100),
        "day_high": q2(max(day_samples)),
        "day_low": q2(min(day_samples)),
        "volume": volume if inst.segment == "EQ" else None,
        "relative_volume": round(0.6 + abs(_noise(f"{symbol}:rv:{now.hour}")) * 1.8, 2),
        "has_options": inst.has_options,
    }


def demo_index_quotes(now: datetime) -> list[dict[str, object]]:
    keys = ("symbol", "name", "exchange", "last", "prev_close", "change", "change_pct")
    return [
        {k: quote(s, now)[k] for k in keys}
        for s, i in INSTRUMENTS.items()
        if i.segment == "INDEX"
    ]


def candles(symbol: str, timeframe: str, now: datetime, limit: int) -> list[dict[str, object]]:
    """Closed candles only (the in-progress bucket is excluded: no repainting)."""
    minutes = TIMEFRAMES[timeframe]
    epoch_min = int(now.timestamp() // 60)
    last_closed_start = (epoch_min // minutes - 1) * minutes
    step = max(1, minutes // 6)
    out = []
    for i in range(limit - 1, -1, -1):
        start = last_closed_start - i * minutes
        samples = [
            price_at(symbol, datetime.fromtimestamp((start + m) * 60, UTC))
            for m in range(0, minutes, step)
        ]
        close = price_at(symbol, datetime.fromtimestamp((start + minutes - 1) * 60, UTC))
        samples.append(close)
        vol = int(abs(_noise(f"{symbol}:{timeframe}:v:{start}")) * 50_000 * minutes ** 0.5 + 500)
        out.append({
            "ts": datetime.fromtimestamp(start * 60, UTC),
            "open": round(samples[0], 2),
            "high": round(max(samples), 2),
            "low": round(min(samples), 2),
            "close": round(close, 2),
            "volume": vol,
        })
    return out


def search(q: str, limit: int = 10) -> list[DemoInstrument]:
    q = q.strip().upper()
    if not q:
        return []
    hits = [
        i for i in INSTRUMENTS.values()
        if q in i.symbol or q in i.name.upper()
    ]
    return sorted(hits, key=lambda i: (not i.symbol.startswith(q), i.symbol))[:limit]
