"""Broker adapter interface.

Every broker integration (paper now, a verified real broker in Phase 8)
implements this protocol. The execution layer never assumes that a successful
call means an order was filled: fills are read back from the broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

BrokerStatus = Literal["OPEN", "PARTIAL", "FILLED", "CANCELLED", "REJECTED"]


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    side: Literal["BUY", "SELL"]
    quantity: int
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: Decimal | None
    contract: dict[str, Any]  # underlying, expiry, strike, option_type, security_id, tick_size


@dataclass(frozen=True)
class BrokerFill:
    trade_id: str
    quantity: int
    price: Decimal
    ts: datetime


@dataclass
class BrokerOrderState:
    broker_order_id: str
    client_order_id: str
    status: BrokerStatus
    filled_qty: int
    avg_price: Decimal | None
    reject_reason: str | None = None
    fills: list[BrokerFill] = field(default_factory=list)


class BrokerError(Exception):
    """Definitive rejection by the broker (safe: the order does not exist)."""


class BrokerUnavailable(Exception):
    """Outcome unknown (timeout, connection reset). Must be reconciled, never retried blindly."""


class BrokerAdapter(Protocol):
    name: str
    supports_live_orders: bool

    def place_order(self, req: OrderRequest) -> BrokerOrderState: ...
    def cancel_order(self, broker_order_id: str) -> BrokerOrderState: ...
    def get_order(self, broker_order_id: str) -> BrokerOrderState: ...
    def find_by_client_id(self, client_order_id: str) -> BrokerOrderState | None: ...
    def match(self, now: datetime) -> int: ...
