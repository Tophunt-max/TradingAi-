"""Thin, read-only HTTP client for the INDstocks API.

Docs: https://api-docs.indstocks.com (conventions, rate limits, error bucket).
This client never places orders. It:
- sends the access token in the `Authorization` header (no "Bearer" prefix),
- applies client-side rate limits per category (data/quote: 5 req/s),
- retries idempotent GETs on 429 / 5xx / network errors with backoff,
- normalises the several error-body shapes the API uses into IndstocksError.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

logger = logging.getLogger("app.indstocks")

Category = Literal["data", "quote", "non_trading"]
RATE_PER_SECOND: dict[Category, int] = {"data": 5, "quote": 5, "non_trading": 15}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class IndstocksError(Exception):
    def __init__(
        self, kind: str, message: str, *, status: int | None = None, detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind  # AUTH | RATE_LIMIT | BAD_REQUEST | UNAVAILABLE | NOT_CONFIGURED
        self.status = status
        self.detail = detail


class _Limiter:
    """Simple per-category sliding window limiter (thread-safe)."""

    def __init__(self, sleep=time.sleep, clock=time.monotonic) -> None:  # type: ignore[no-untyped-def]
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._sleep, self._clock = sleep, clock

    def acquire(self, category: Category) -> None:
        limit = RATE_PER_SECOND[category]
        while True:
            with self._lock:
                now = self._clock()
                hits = [t for t in self._hits.get(category, []) if now - t < 1.0]
                if len(hits) < limit:
                    hits.append(now)
                    self._hits[category] = hits
                    return
                wait = 1.0 - (now - hits[0])
                self._hits[category] = hits
            self._sleep(max(wait, 0.01))


def _parse_error(resp: httpx.Response) -> IndstocksError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    detail = body.get("debug_info") or body.get("error")
    message = body.get("message") or detail or f"HTTP {resp.status_code}"
    error_type = body.get("error_type") or ""
    status = resp.status_code
    if status in (401, 403) or error_type == "TokenException" or "authorization" in message.lower():
        kind = "AUTH"
    elif status == 429:
        kind = "RATE_LIMIT"
    elif status >= 500 or error_type in ("NetworkException", "ServiceUnavailableException"):
        kind = "UNAVAILABLE"
    else:
        kind = "BAD_REQUEST"
    return IndstocksError(kind, str(message), status=status, detail=str(detail) if detail else None)


@dataclass
class IndstocksClient:
    base_url: str
    token: str | None
    timeout: float = 8.0
    max_retries: int = 2
    transport: httpx.BaseTransport | None = None
    sleep: Any = time.sleep

    def __post_init__(self) -> None:
        self._http = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout,
            transport=self.transport,
            headers={"Accept": "application/json"},
        )
        self._limiter = _Limiter(sleep=self.sleep)

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any], category: Category) -> httpx.Response:
        if not self.token:
            raise IndstocksError("NOT_CONFIGURED", "INDSTOCKS_ACCESS_TOKEN is not set.")
        last: IndstocksError | None = None
        for attempt in range(self.max_retries + 1):
            self._limiter.acquire(category)
            try:
                resp = self._http.get(path, params=params, headers={"Authorization": self.token})
            except httpx.TransportError as exc:
                last = IndstocksError("UNAVAILABLE", f"Network error: {type(exc).__name__}")
            else:
                if resp.status_code < 400:
                    return resp
                last = _parse_error(resp)
                if resp.status_code not in RETRYABLE_STATUS:
                    raise last
            if attempt < self.max_retries:
                backoff = 0.5 * (2**attempt)
                logger.warning(
                    "indstocks_retry",
                    extra={"path": path, "attempt": attempt + 1, "kind": last.kind},
                )
                self.sleep(backoff)
        assert last is not None
        raise last

    def get_json(
        self, path: str, params: dict[str, Any] | None = None, category: Category = "data"
    ) -> Any:
        resp = self._get(path, params or {}, category)
        try:
            body = resp.json()
        except ValueError as exc:
            raise IndstocksError("BAD_REQUEST", "Provider returned non-JSON.") from exc
        # Some endpoints wrap with {"status": "success"}, others with {"success": true}.
        if isinstance(body, dict) and (
            body.get("status") == "error" or body.get("success") is False
        ):
            raise IndstocksError("BAD_REQUEST", str(body.get("message", "Provider error")))
        return body.get("data") if isinstance(body, dict) and "data" in body else body

    def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        return self._get(path, params or {}, "data").text

    # ------------------------------------------------------------ endpoints

    def profile(self) -> dict[str, Any]:
        return self.get_json("/user/profile", category="non_trading")

    def instruments_csv(self, source: Literal["equity", "fno", "index"]) -> str:
        return self.get_text("/market/instruments", {"source": source})

    def quotes_full(self, scrip_codes: list[str]) -> dict[str, Any]:
        return self.get_json(
            "/market/quotes/full", {"scrip-codes": ",".join(scrip_codes)}, category="quote"
        )

    def historical(
        self, interval: str, scrip_codes: list[str], start_ms: int, end_ms: int
    ) -> dict[str, Any]:
        if len(scrip_codes) > 5:
            raise ValueError("INDstocks allows at most 5 scrip codes per historical call.")
        return self.get_json(
            f"/market/historical/{interval}",
            {"scrip-codes": ",".join(scrip_codes), "start_time": start_ms, "end_time": end_ms},
        )

    def expiries(self, underlying: str) -> list[str]:
        return self.get_json(
            "/market/instruments/expiries", {"underlying": underlying, "segment": "DERIVATIVE"}
        )

    def option_chain(
        self, *, exchange: str, segment: str, underlying_scrip: str, expiry: str, strike_count: int
    ) -> dict[str, Any]:
        return self.get_json(
            "/market/option-chain",
            {
                "exchange": exchange,
                "segment": segment,
                "underlying-scrip": underlying_scrip,
                "expiry": expiry,
                "strike_count": strike_count,
            },
        )
