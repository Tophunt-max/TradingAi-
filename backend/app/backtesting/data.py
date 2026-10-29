"""Bar data preparation and causal (past-only) views.

The engine computes indicator series once over the whole history. Every series
in `app.indicators` / `app.analysis.context` is causal (value[i] uses bars
0..i only), so slicing at the decision bar gives the same values a live bot
would have seen. Strategies never receive the full arrays:

- condition strategies get a `Ctx` whose lists are physical copies ending at
  the decision bar, so future bars do not exist for them;
- Python strategy hooks get a `BarView` whose accessors raise
  `LookaheadError` for any index after the decision bar.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import datetime, time
from typing import Any

from app.analysis.context import Ctx
from app.core.time import IST

# NSE cash/F&O session. Bars starting outside it are dropped (DEMO data is 24x7).
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


class LookaheadError(IndexError):
    """A strategy asked for a bar after the decision bar."""


def session_bars(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Weekday bars whose start time is inside the NSE session (IST)."""
    out = []
    for k in candles:
        t = k["ts"].astimezone(IST)
        if t.weekday() < 5 and SESSION_OPEN <= t.time() < SESSION_CLOSE:
            out.append(k)
    return out


def fingerprint(candles: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for k in candles:
        h.update(json.dumps([k["ts"].isoformat(), float(k["open"]), float(k["high"]),
                             float(k["low"]), float(k["close"]),
                             float(k.get("volume") or 0)]).encode())
    return h.hexdigest()[:16]


def slice_ctx(full: Ctx, start: int, end: int) -> Ctx:
    """Copy of bars [start, end) with every indicator series sliced the same way."""
    return Ctx(candles=full.candles[start:end], ts=full.ts[start:end], o=full.o[start:end],
               h=full.h[start:end], l=full.l[start:end], c=full.c[start:end],
               v=full.v[start:end], s={k: v[start:end] for k, v in full.s.items()},
               intraday=full.intraday)


class PastView(Sequence[Any]):
    """Read-only view of a series that ends at the cursor (decision bar)."""

    __slots__ = ("_d", "_n")

    def __init__(self, data: Sequence[Any], cursor: int) -> None:
        self._d, self._n = data, cursor + 1

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, k: Any) -> Any:  # type: ignore[override]
        if isinstance(k, slice):
            return list(self._d[: self._n])[k]
        if k < 0:
            k += self._n
            if k < 0:
                raise IndexError(k)
        if k >= self._n:
            raise LookaheadError(f"Bar {k} is after the decision bar {self._n - 1}.")
        return self._d[k]

    def __iter__(self) -> Iterator[Any]:
        for k in range(self._n):
            yield self._d[k]


class BarView:
    """What a Python strategy hook sees at decision bar `i` (close of bar i)."""

    def __init__(self, full: Ctx, i: int) -> None:
        self._full, self.i = full, i
        self.ts = PastView(full.ts, i)
        self.open = PastView(full.o, i)
        self.high = PastView(full.h, i)
        self.low = PastView(full.l, i)
        self.close = PastView(full.c, i)
        self.volume = PastView(full.v, i)

    def series(self, name: str) -> PastView:
        return PastView(self._full.s[name], self.i)

    @property
    def now(self) -> datetime:
        return self._full.ts[self.i]
