"""Synthetic DEMO market data.

Values are generated from a deterministic random walk. They are NOT real
prices, must never feed trading decisions, and every payload is labelled
`source="DEMO"` / `is_demo=True`.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

DEMO_DISCLAIMER = "DEMO data: synthetic values for UI testing only. Not real market prices."


@dataclass(frozen=True)
class DemoIndex:
    symbol: str
    name: str
    exchange: str
    base: float
    vol: float  # daily % volatility used by the walk


DEMO_INDICES = (
    DemoIndex("NIFTY", "NIFTY 50 (demo)", "NSE", 24000.0, 0.8),
    DemoIndex("BANKNIFTY", "NIFTY BANK (demo)", "NSE", 52000.0, 1.1),
    DemoIndex("FINNIFTY", "NIFTY FIN SERVICE (demo)", "NSE", 23500.0, 0.9),
    DemoIndex("SENSEX", "S&P BSE SENSEX (demo)", "BSE", 79000.0, 0.8),
)


def _noise(key: str) -> float:
    """Deterministic value in [-1, 1] for a key."""
    h = hashlib.sha256(key.encode()).digest()
    return (int.from_bytes(h[:8], "big") / 2**64) * 2 - 1


def _q(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def demo_index_quotes(now: datetime) -> list[dict[str, object]]:
    day = now.strftime("%Y-%m-%d")
    minute_of_day = now.hour * 60 + now.minute
    out = []
    for idx in DEMO_INDICES:
        prev_close = idx.base * (1 + _noise(f"{idx.symbol}:{day}:open") * 0.02)
        # Smooth intraday drift: sum of a few sinusoids with seeded phases.
        drift = sum(
            math.sin(minute_of_day / (17 * k) + _noise(f"{idx.symbol}:{day}:{k}") * math.pi) / k
            for k in (1, 2, 3)
        )
        last = prev_close * (1 + drift * idx.vol / 100 / 1.8)
        change = last - prev_close
        out.append({
            "symbol": idx.symbol,
            "name": idx.name,
            "exchange": idx.exchange,
            "last": _q(last),
            "prev_close": _q(prev_close),
            "change": _q(change),
            "change_pct": _q(change / prev_close * 100),
        })
    return out
