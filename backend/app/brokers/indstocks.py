"""INDstocks (INDmoney) broker client. Status: UNVERIFIED_AGAINST_LIVE_API.

Every endpoint and field below is taken from the official documentation
(https://api-docs.indstocks.com, checked 2026-10-29):
- Users:      GET /user/profile, GET /funds                      (Users/)
- Portfolio:  GET /portfolio/positions?segment=derivative         (portfolio_funds/)
- Orders:     POST /order, POST /order/modify, POST /order/cancel,
              GET /order-book, GET /order (JSON body), GET /order/trades (JSON body),
              GET /trade-book?segment=DERIVATIVE                  (normal_orders/)
- Conventions: `Authorization: <token>` (no "Bearer"), rate limits: order APIs 10/s,
  non-trading APIs 15/s                                           (conventions/)

It has only been exercised against an in-memory fake built from those documented shapes
(backend/tests/fake_indstocks_broker.py). Documented facts this client relies on:
- `remarks` (≤100 chars) is stored with the order and echoed by GET /order, /order-book and
  /trade-book. We send our client_order_id there and use it to find an order whose
  placement outcome is unknown. The docs state it is NOT an idempotency key, so `place` is
  never retried.
- MARKET orders are converted to LIMIT orders with a price-protection band by INDstocks.
- Order placement needs a whitelisted static IP; read-only endpoints do not.
- Tokens last at most 24 hours and can only be revoked from the INDstocks dashboard
  (no revoke endpoint is documented).
- Streaming (order-updates WebSocket) exists at INDstocks but is not implemented here.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

from app.brokers.base import (
    BrokerError,
    BrokerFill,
    BrokerOrderState,
    BrokerUnavailable,
    OrderRequest,
)
from app.brokers.client import (
    BrokerAuthError,
    BrokerCapabilities,
    BrokerFunds,
    BrokerOrder,
    BrokerPosition,
    BrokerProfile,
    BrokerTrade,
)
from app.core.time import IST
from app.market_data.indstocks.client import IndstocksError, _parse_error

logger = logging.getLogger("app.brokers.indstocks")

SEGMENT = "DERIVATIVE"
Category = Literal["order", "non_trading"]
RATE_PER_SECOND: dict[str, int] = {"order": 10, "non_trading": 15}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

CAPABILITIES = BrokerCapabilities(
    order_types=("MARKET", "LIMIT"),
    product_types=("INTRADAY", "MARGIN"),
    segments=("DERIVATIVE",),
    streaming=False,
    modify=True,
    cancel=True,
    tradebook=True,
    find_by_client_tag=True,
    revoke_token=False,
    notes=(
        "MARKET orders are converted by INDstocks to LIMIT orders with a price-protection "
        "band.",
        "Order placement requires a static IP whitelisted on the INDstocks dashboard.",
        "Access tokens last at most 24 hours; revoke them on the INDstocks dashboard.",
        "Order-update streaming is offered by INDstocks but not implemented here.",
    ),
)

# INDstocks order statuses (normal_orders/ "Order Status Types") -> broker-neutral status.
STATUS_MAP = {
    "QUEUED": "OPEN", "O-PENDING": "OPEN", "SL-PENDING": "OPEN", "PROCESSING": "OPEN",
    "INITIATED": "OPEN", "MODIFIED": "OPEN", "PENDING": "OPEN",
    "SUCCESS": "FILLED",
    "PARTIALLY FILLED": "PARTIAL",
    "CANCELLED": "CANCELLED", "EXPIRED": "CANCELLED",
    "PARTIALLY FILLED - CANCELLED": "CANCELLED", "PARTIALLY FILLED - EXPIRED": "CANCELLED",
    "ABORTED": "REJECTED", "FAILED": "REJECTED",
}


def normalise_status(raw: str | None) -> str:
    # Unknown statuses are treated as still open: the order is re-read, never assumed done.
    return STATUS_MAP.get((raw or "").strip().upper(), "OPEN")


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _int(v: Any) -> int:
    d = _dec(v)
    return int(d) if d is not None else 0


def _ts(v: Any) -> datetime | None:
    """ISO 8601 (the docs show +05:30 offsets); naive values are IST."""
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=IST)
    return t.astimezone(UTC)


class _Limiter:
    def __init__(self, sleep=time.sleep, clock=time.monotonic) -> None:  # type: ignore[no-untyped-def]
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._sleep, self._clock = sleep, clock

    def acquire(self, category: str) -> None:
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


def _raise_for(exc: IndstocksError, *, unknown_outcome: bool) -> None:
    if exc.kind == "AUTH":
        raise BrokerAuthError(f"INDstocks refused the access token: {exc}") from exc
    if exc.kind in ("UNAVAILABLE", "RATE_LIMIT") or (unknown_outcome and exc.kind != "BAD_REQUEST"):
        raise BrokerUnavailable(f"INDstocks did not confirm the request: {exc}") from exc
    raise BrokerError(f"INDstocks rejected the request: {exc}") from exc


@dataclass
class IndstocksBrokerClient:
    token: str
    base_url: str = "https://api.indstocks.com"
    product: str = "INTRADAY"
    timeout: float = 8.0
    max_retries: int = 2
    transport: httpx.BaseTransport | None = None
    sleep: Any = time.sleep

    name = "indstocks"
    capabilities = CAPABILITIES

    def __post_init__(self) -> None:
        self._http = httpx.Client(base_url=self.base_url.rstrip("/"), timeout=self.timeout,
                                  transport=self.transport,
                                  headers={"Accept": "application/json"})
        self._limiter = _Limiter(sleep=self.sleep)

    def __repr__(self) -> str:  # never print the token
        return f"IndstocksBrokerClient(base_url={self.base_url!r}, product={self.product!r})"

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ transport

    def _send(self, method: str, path: str, *, category: Category, retry: bool,
              params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
        last: IndstocksError | None = None
        attempts = self.max_retries + 1 if retry else 1
        for attempt in range(attempts):
            self._limiter.acquire(category)
            try:
                resp = self._http.request(method, path, params=params, json=body,
                                          headers={"Authorization": self.token})
            except httpx.TransportError as exc:
                last = IndstocksError("UNAVAILABLE", f"Network error: {type(exc).__name__}")
            else:
                if resp.status_code < 400:
                    try:
                        payload = resp.json()
                    except ValueError as exc:
                        raise IndstocksError("UNAVAILABLE", "Non-JSON response.") from exc
                    if isinstance(payload, dict) and (payload.get("status") == "error"
                                                      or payload.get("success") is False):
                        raise IndstocksError("BAD_REQUEST",
                                             str(payload.get("message", "Broker error")))
                    return payload.get("data") if isinstance(payload, dict) else payload
                last = _parse_error(resp)
                if resp.status_code not in RETRYABLE_STATUS:
                    raise last
            if attempt < attempts - 1:
                logger.warning("indstocks_broker_retry",
                               extra={"path": path, "attempt": attempt + 1, "kind": last.kind})
                self.sleep(0.5 * (2 ** attempt))
        assert last is not None
        raise last

    def _read(self, path: str, *, params: dict[str, Any] | None = None,
              body: dict[str, Any] | None = None, category: Category = "non_trading") -> Any:
        try:
            return self._send("GET", path, category=category, retry=True, params=params,
                              body=body)
        except IndstocksError as exc:
            _raise_for(exc, unknown_outcome=False)
            raise  # pragma: no cover

    def _write(self, path: str, body: dict[str, Any], *, unknown_outcome: bool) -> Any:
        try:
            return self._send("POST", path, category="order", retry=False, body=body)
        except IndstocksError as exc:
            _raise_for(exc, unknown_outcome=unknown_outcome)
            raise  # pragma: no cover

    # ------------------------------------------------------------ read-only

    def profile(self) -> BrokerProfile:
        d = self._read("/user/profile") or {}
        fno = d.get("is_nse_fno_onboarded")
        scopes = ["profile", "funds", "positions", "orders.read"]
        if fno:
            scopes.append("fno")
        return BrokerProfile(fno_enabled=bool(fno) if fno is not None else None,
                             scopes=tuple(scopes))

    def funds(self) -> BrokerFunds:
        d = self._read("/funds") or {}
        avl = d.get("detailed_avl_balance") or {}
        return BrokerFunds(
            available_option_buy=_dec(avl.get("option_buy")),
            start_of_day_balance=_dec(d.get("sod_balance")),
            withdrawable=_dec(d.get("withdrawal_balance")),
            realized_pnl=_dec(d.get("realized_pnl")), unrealized_pnl=_dec(d.get("unrealized_pnl")),
            brokerage_today=_dec(d.get("brokerage")), charges_today=_dec(d.get("fno_charges")),
            fetched_at=datetime.now(UTC))

    def positions(self) -> list[BrokerPosition]:
        rows = self._read("/portfolio/positions", params={"segment": "derivative"}) or []
        return [BrokerPosition(
            position_id=str(r.get("position_id", "")), security_id=str(r.get("security_id", "")),
            symbol=str(r.get("symbol", "")), segment=str(r.get("segment", "")),
            product=str(r.get("product", "")), net_qty=_int(r.get("net_qty")),
            avg_price=_dec(r.get("avg_price")), buy_qty=_int(r.get("buy_qty")),
            buy_avg=_dec(r.get("buy_avg")), sell_qty=_int(r.get("sell_qty")),
            sell_avg=_dec(r.get("sell_avg")), realized_pnl=_dec(r.get("realized_profit")),
            option_type=r.get("drv_option_type") or None, strike=_dec(r.get("drv_strike_price")),
            expiry=r.get("drv_expiry_date") or None) for r in rows]

    def _order(self, r: dict[str, Any]) -> BrokerOrder:
        raw = str(r.get("status", ""))
        return BrokerOrder(
            broker_order_id=str(r.get("id", "")), client_tag=r.get("remarks") or None,
            status=normalise_status(raw), raw_status=raw, side=str(r.get("txn_type", "")),
            security_id=str(r.get("security_id", "")), name=str(r.get("name", "")),
            product=str(r.get("product", "")), order_type=str(r.get("order_type", "")),
            quantity=_int(r.get("requested_qty")), filled_qty=_int(r.get("traded_qty")),
            avg_price=_dec(r.get("traded_price")),
            exchange_order_id=r.get("exch_order_id") or None,
            reject_reason=r.get("extra_info") or None, created_at=_ts(r.get("created_at")))

    def orders(self) -> list[BrokerOrder]:
        return [self._order(r) for r in (self._read("/order-book") or [])]

    def tradebook(self) -> list[BrokerTrade]:
        rows = self._read("/trade-book", params={"segment": SEGMENT}) or []
        return [BrokerTrade(trade_id=str(r.get("fill_id")),
                            exchange_order_id=r.get("exch_order_id") or None,
                            security_id=r.get("scrip_code") or None,
                            quantity=_int(r.get("quantity")),
                            price=_dec(r.get("price")) or Decimal("0"),
                            ts=_ts(r.get("trade_date")) or datetime.now(UTC),
                            client_tag=r.get("remarks") or None) for r in rows]

    # ------------------------------------------------------------ order path

    def _state(self, o: BrokerOrder, fills: list[BrokerFill]) -> BrokerOrderState:
        filled = sum(f.quantity for f in fills) if fills else o.filled_qty
        avg = o.avg_price
        if fills and filled:
            avg = (sum((f.price * f.quantity for f in fills), Decimal("0")) / filled
                   ).quantize(Decimal("0.0001"))
        return BrokerOrderState(broker_order_id=o.broker_order_id,
                                client_order_id=o.client_tag or "", status=o.status,  # type: ignore[arg-type]
                                filled_qty=filled, avg_price=avg,
                                reject_reason=o.reject_reason if o.status == "REJECTED" else None,
                                fills=fills)

    def _fills(self, broker_order_id: str) -> list[BrokerFill]:
        rows = self._read("/order/trades",
                          body={"order_id": broker_order_id, "segment": SEGMENT}) or []
        return [BrokerFill(trade_id=str(r.get("fill_id")), quantity=_int(r.get("quantity")),
                           price=_dec(r.get("price")) or Decimal("0"),
                           ts=_ts(r.get("trade_date")) or datetime.now(UTC)) for r in rows]

    def get(self, broker_order_id: str) -> BrokerOrderState:
        d = self._read("/order", body={"order_id": broker_order_id, "segment": SEGMENT})
        if not isinstance(d, dict):
            raise BrokerError(f"INDstocks has no order {broker_order_id}.")
        o = self._order(d)
        return self._state(o, self._fills(o.broker_order_id) if o.filled_qty else [])

    def find_by_client_id(self, client_order_id: str) -> BrokerOrderState | None:
        """Looks the order up by the `remarks` tag in today's order book. Never resends."""
        matches = [o for o in self.orders()
                   if (o.client_tag or "").strip() == client_order_id[:100]]
        if not matches:
            return None
        if len(matches) > 1:
            logger.error("indstocks_duplicate_client_tag", extra={"count": len(matches)})
        o = min(matches, key=lambda m: m.created_at or datetime.max.replace(tzinfo=UTC))
        return self._state(o, self._fills(o.broker_order_id) if o.filled_qty else [])

    def place(self, req: OrderRequest) -> BrokerOrderState:
        sid = req.contract.get("security_id")
        if not sid:
            raise BrokerError("The contract has no INDstocks security_id.")
        body: dict[str, Any] = {
            "txn_type": req.side, "exchange": str(req.contract.get("exchange") or "NSE"),
            "segment": SEGMENT, "product": self.product, "order_type": req.order_type,
            "validity": "DAY", "security_id": str(sid), "qty": req.quantity,
            "remarks": req.client_order_id[:100],
        }
        if req.order_type == "LIMIT":
            if req.limit_price is None:
                raise BrokerError("A LIMIT order needs a limit price.")
            body["limit_price"] = float(req.limit_price)
        d = self._write("/order", body, unknown_outcome=True) or {}
        oid = d.get("order_id")
        if not oid:
            raise BrokerUnavailable("INDstocks returned no order id; reconcile by client id.")
        return BrokerOrderState(broker_order_id=str(oid), client_order_id=req.client_order_id,
                                status=normalise_status(d.get("order_status")),  # type: ignore[arg-type]
                                filled_qty=0, avg_price=None)

    def modify(self, broker_order_id: str, *, quantity: int,
               limit_price: Decimal) -> BrokerOrderState:
        self._write("/order/modify", {"order_id": broker_order_id, "segment": SEGMENT,
                                      "qty": quantity, "limit_price": float(limit_price)},
                    unknown_outcome=False)
        return self.get(broker_order_id)

    def cancel(self, broker_order_id: str) -> BrokerOrderState:
        self._write("/order/cancel", {"order_id": broker_order_id, "segment": SEGMENT},
                    unknown_outcome=False)
        return self.get(broker_order_id)

    def health(self) -> dict[str, Any]:
        """Live, read-only check (GET /user/profile)."""
        try:
            self.profile()
        except BrokerAuthError:
            return {"ok": False, "status": "EXPIRED",
                    "detail": "INDstocks refused the access token (expired or revoked)."}
        except (BrokerUnavailable, BrokerError) as exc:
            return {"ok": False, "status": "ERROR", "detail": str(exc)[:300]}
        return {"ok": True, "status": "CONNECTED", "detail": "INDstocks profile reachable."}
