"""Fixed-window rate limiter.

Uses Redis when REDIS_URL is configured (shared across API replicas), otherwise
an in-process store suitable for development and tests only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import RateLimitedError

logger = logging.getLogger("app.rate_limit")


class RateLimitStore(Protocol):
    def hit(self, key: str, window_seconds: int) -> int: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, window_seconds: int) -> int:
        now = time.monotonic()
        with self._lock:
            count, expires = self._data.get(key, (0, now + window_seconds))
            if now >= expires:
                count, expires = 0, now + window_seconds
            count += 1
            self._data[key] = (count, expires)
            return count

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, socket_timeout=0.5)

    def hit(self, key: str, window_seconds: int) -> int:
        pipe = self._client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = pipe.execute()
        return int(count)


_store: RateLimitStore | None = None


def get_store() -> RateLimitStore:
    global _store
    if _store is None:
        url = get_settings().redis_url
        _store = RedisStore(url) if url else MemoryStore()
    return _store


def set_store(store: RateLimitStore | None) -> None:
    global _store
    _store = store


def client_ip(request: Request) -> str:
    # Behind a trusted proxy, configure uvicorn --proxy-headers / forwarded-allow-ips
    # so request.client reflects the real client. X-Forwarded-For is not trusted here.
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, limit: int | None = None, window: int | None = None) -> Callable:
    def dependency(request: Request) -> None:
        settings = get_settings()
        max_hits = limit or settings.auth_rate_limit
        win = window or settings.auth_rate_window_seconds
        key = f"rl:{scope}:{client_ip(request)}"
        try:
            count = get_store().hit(key, win)
        except Exception:  # Redis outage: fail open but log loudly.
            logger.warning("rate_limit_store_unavailable", extra={"scope": scope})
            return
        if count > max_hits:
            raise RateLimitedError(
                "Too many requests. Please try again later.",
                headers={"Retry-After": str(win)},
            )

    return dependency
