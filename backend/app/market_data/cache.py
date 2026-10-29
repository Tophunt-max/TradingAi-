from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class TTLCache:
    """Tiny in-process TTL cache to stay inside provider rate limits."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_items: int = 2000) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._max = max_items

    def get_or_set(self, key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > now:
                return hit[1]
        value = fn()
        with self._lock:
            if len(self._data) >= self._max:
                self._data = {k: v for k, v in self._data.items() if v[0] > now}
            self._data[key] = (now + ttl, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
