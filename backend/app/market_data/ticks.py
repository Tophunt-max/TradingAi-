"""Latest-tick store shared between the price-feed worker and the API.

Redis when REDIS_URL is set (required for a separate worker process);
otherwise an in-process dict, which only helps tests and single-process dev.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Protocol


class TickStore(Protocol):
    def put(self, code: str, ltp: float, ts_ms: int) -> bool: ...
    def get(self, code: str) -> tuple[float, datetime] | None: ...
    def heartbeat(self, ts_ms: int) -> None: ...
    def last_heartbeat(self) -> datetime | None: ...


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


class MemoryTickStore:
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, int]] = {}
        self._hb: int | None = None
        self._lock = threading.Lock()

    def put(self, code: str, ltp: float, ts_ms: int) -> bool:
        """Store a tick unless it is older than the one we have (out-of-order/duplicate)."""
        with self._lock:
            cur = self._d.get(code)
            if cur and cur[1] >= ts_ms:
                return False
            self._d[code] = (ltp, ts_ms)
            return True

    def get(self, code: str) -> tuple[float, datetime] | None:
        with self._lock:
            cur = self._d.get(code)
        return (cur[0], _dt(cur[1])) if cur else None

    def heartbeat(self, ts_ms: int) -> None:
        self._hb = ts_ms

    def last_heartbeat(self) -> datetime | None:
        return _dt(self._hb) if self._hb else None


class RedisTickStore:
    _PUT = """
    local cur = redis.call('HGET', KEYS[1], ARGV[1])
    if cur then
      local ts = tonumber(cjson.decode(cur)['t'])
      if ts >= tonumber(ARGV[3]) then return 0 end
    end
    redis.call('HSET', KEYS[1], ARGV[1], cjson.encode({p=tonumber(ARGV[2]), t=tonumber(ARGV[3])}))
    return 1
    """

    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, socket_timeout=0.5)
        self._put = self._r.register_script(self._PUT)

    def put(self, code: str, ltp: float, ts_ms: int) -> bool:
        return bool(self._put(keys=["ticks"], args=[code, ltp, ts_ms]))

    def get(self, code: str) -> tuple[float, datetime] | None:
        raw = self._r.hget("ticks", code)
        if not raw:
            return None
        d = json.loads(raw)
        return float(d["p"]), _dt(int(d["t"]))

    def heartbeat(self, ts_ms: int) -> None:
        self._r.set("ticks:heartbeat", ts_ms)

    def last_heartbeat(self) -> datetime | None:
        v = self._r.get("ticks:heartbeat")
        return _dt(int(v)) if v else None


_store: TickStore | None = None


def get_tick_store(redis_url: str | None) -> TickStore:
    global _store
    if _store is None:
        _store = RedisTickStore(redis_url) if redis_url else MemoryTickStore()
    return _store


def set_tick_store(store: TickStore | None) -> None:
    global _store
    _store = store
