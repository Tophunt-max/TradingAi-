"""Execution adapter interface.

Every way of executing an order implements `ExecutionAdapter`:
- `PaperExecutionAdapter` (DEMO accounts): the in-house simulator.
- `RealBrokerExecutionAdapter` (REAL accounts): a broker-neutral wrapper around a
  `BrokerClient`; locked in this build.

An adapter is bound to one account and carries that account's mode. It refuses requests
for any other mode, so a DEMO order can never reach a real broker and vice versa. The
execution layer never assumes that a successful call means an order was filled: fills are
read back from the adapter.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

BrokerStatus = Literal["OPEN", "PARTIAL", "FILLED", "CANCELLED", "REJECTED"]
AccountMode = Literal["DEMO", "REAL"]


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    side: Literal["BUY", "SELL"]
    quantity: int
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: Decimal | None
    contract: dict[str, Any]  # underlying, expiry, strike, option_type, security_id, tick_size
    # The account the order belongs to; adapters check it matches their own account.
    account_id: uuid.UUID | None = None
    account_mode: str | None = None


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


class LiveTradingLocked(BrokerError):
    """A REAL order was refused before reaching any broker. Carries the blocking reasons."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Live trading is locked: " + " ".join(reasons))
        self.reasons = reasons


class AdapterAccountMismatch(RuntimeError):
    """An adapter was given an account or order of the wrong mode (or another account)."""


class ExecutionAdapter(ABC):
    name: str
    account_mode: AccountMode
    supports_live_orders: bool = False

    def __init__(self, account: Any) -> None:
        if getattr(account, "mode", None) != self.account_mode:
            raise AdapterAccountMismatch(
                f"{type(self).__name__} serves {self.account_mode} accounts only; got a "
                f"{getattr(account, 'mode', None)} account.")
        self.account = account

    def check_request(self, req: OrderRequest) -> None:
        if req.account_mode != self.account_mode or (
                req.account_id is not None and req.account_id != self.account.id):
            raise AdapterAccountMismatch(
                f"{type(self).__name__} refused an order for a {req.account_mode} account.")

    @abstractmethod
    def submit(self, req: OrderRequest) -> BrokerOrderState: ...

    @abstractmethod
    def cancel(self, broker_order_id: str) -> BrokerOrderState: ...

    @abstractmethod
    def get_order(self, broker_order_id: str) -> BrokerOrderState: ...

    @abstractmethod
    def find_by_client_id(self, client_order_id: str) -> BrokerOrderState | None: ...

    @abstractmethod
    def poll(self, now: datetime) -> int:
        """Advance/collect fills and status for this account's open orders."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """{'ok': bool, 'detail': str}"""


# Backwards-compatible name for the old protocol.
BrokerAdapter = ExecutionAdapter
