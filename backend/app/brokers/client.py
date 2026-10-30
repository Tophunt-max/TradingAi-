"""Broker-neutral client interface (`BrokerClient`) and its typed results.

A broker integration implements `BrokerClient` with the broker's official API only. The
execution layer reaches it exclusively through `RealBrokerExecutionAdapter` (locked in this
build); the connection service uses the read-only methods for token validation and sync.

Rules every implementation follows:
- Money and prices are `Decimal`; timestamps are timezone-aware UTC.
- `place` is never retried: a timeout or 5xx raises `BrokerUnavailable` (outcome unknown,
  reconcile with `find_by_client_id`); a definitive refusal raises `BrokerError`.
- Read calls may retry transient errors. An invalid or expired token raises `BrokerAuthError`.
- Capabilities describe what this integration implements, not what the broker offers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.brokers.base import BrokerError, BrokerOrderState, OrderRequest


class AuthFlow(StrEnum):
    OAUTH_REDIRECT = "OAUTH_REDIRECT"  # broker login page -> redirect with a request token
    TOKEN_PASTE = "TOKEN_PASTE"  # noqa: S105 (enum label, not a secret); user generates an access token at the broker and pastes it


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"  # exercised against the broker's live API
    UNVERIFIED_AGAINST_LIVE_API = "UNVERIFIED_AGAINST_LIVE_API"  # docs + fake transport only
    NOT_INTEGRATED = "NOT_INTEGRATED"


class BrokerAuthError(BrokerError):
    """The token was refused (invalid, expired or revoked). Nothing was executed."""


class NotVerified(NotImplementedError):
    """The broker documents no endpoint for this operation, so it is not implemented."""


@dataclass(frozen=True)
class BrokerCapabilities:
    order_types: tuple[str, ...]
    product_types: tuple[str, ...]
    segments: tuple[str, ...]
    streaming: bool  # order/price streaming implemented in this integration
    modify: bool
    cancel: bool
    tradebook: bool
    find_by_client_tag: bool
    revoke_token: bool  # the broker exposes an API to revoke the access token
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}


@dataclass(frozen=True)
class BrokerFunds:
    available_option_buy: Decimal | None  # buying power for option buying
    start_of_day_balance: Decimal | None
    withdrawable: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    brokerage_today: Decimal | None
    charges_today: Decimal | None  # segment charges accrued today (F&O for this platform)
    fetched_at: datetime


@dataclass(frozen=True)
class BrokerPosition:
    position_id: str
    security_id: str
    symbol: str
    segment: str
    product: str
    net_qty: int
    avg_price: Decimal | None
    buy_qty: int
    buy_avg: Decimal | None
    sell_qty: int
    sell_avg: Decimal | None
    realized_pnl: Decimal | None
    option_type: str | None = None
    strike: Decimal | None = None
    expiry: str | None = None


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_tag: str | None  # our client_order_id, echoed by the broker
    status: str  # normalised: OPEN | PARTIAL | FILLED | CANCELLED | REJECTED
    raw_status: str
    side: str
    security_id: str
    name: str
    product: str
    order_type: str
    quantity: int
    filled_qty: int
    avg_price: Decimal | None
    exchange_order_id: str | None
    reject_reason: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class BrokerTrade:
    trade_id: str
    exchange_order_id: str | None
    security_id: str | None
    quantity: int
    price: Decimal
    ts: datetime
    client_tag: str | None = None


@dataclass(frozen=True)
class BrokerProfile:
    """What a read-only token check learns. Identity fields are not stored."""

    fno_enabled: bool | None
    scopes: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class BrokerClient(Protocol):
    """What a specific broker integration must provide."""

    name: str

    # Order path (used only by RealBrokerExecutionAdapter, which is locked).
    def place(self, req: OrderRequest) -> BrokerOrderState: ...
    def modify(self, broker_order_id: str, *, quantity: int,
               limit_price: Decimal) -> BrokerOrderState: ...
    def cancel(self, broker_order_id: str) -> BrokerOrderState: ...
    def get(self, broker_order_id: str) -> BrokerOrderState: ...
    def find_by_client_id(self, client_order_id: str) -> BrokerOrderState | None: ...

    # Read-only account data.
    def profile(self) -> BrokerProfile: ...
    def funds(self) -> BrokerFunds: ...
    def positions(self) -> list[BrokerPosition]: ...
    def orders(self) -> list[BrokerOrder]: ...
    def tradebook(self) -> list[BrokerTrade]: ...

    def health(self) -> dict[str, Any]: ...


def to_jsonable(value: Any) -> Any:
    """Dataclasses / Decimals / datetimes -> JSON-safe values (Decimals as strings)."""
    if hasattr(value, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
