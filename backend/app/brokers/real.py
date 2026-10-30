"""REAL-account execution adapter (locked in this build).

`RealBrokerExecutionAdapter` is broker-neutral: it drives a `BrokerClient`
(app/brokers/client.py), normally the client bound to the account's broker connection
(app/brokers/connections.py). Before every submit it checks, in order:
1. LIVE_TRADING_ENABLED (Settings refuses true in this build, so this always blocks),
2. the account is REAL (a DEMO account is refused at construction),
3. the account is ACTIVE,
4. an unrevoked explicit live authorisation exists for this user and account,
5. the broker client reports healthy (connection CONNECTED and token unexpired).
Any failure raises `LiveTradingLocked` with every reason; nothing reaches the client.
Cancel/get/find_by_client_id are read or risk-reducing and go to the client directly.
Real P&L comes only from broker-confirmed fills read back through `get`/`find_by_client_id`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import (
    BrokerOrderState,
    BrokerUnavailable,
    ExecutionAdapter,
    LiveTradingLocked,
    OrderRequest,
)
from app.brokers.client import BrokerClient
from app.core.config import Settings
from app.models.accounts import LiveAuthorization


class UnavailableBrokerClient:
    """Used when a REAL account has no usable broker connection. Every call fails."""

    name = "NONE"

    def __init__(self, detail: str = "No broker is connected for this account.") -> None:
        self.detail = detail

    def _fail(self) -> BrokerUnavailable:
        return BrokerUnavailable(self.detail)

    def place(self, req: OrderRequest) -> BrokerOrderState:
        raise self._fail()

    def modify(self, broker_order_id: str, *, quantity: int,
               limit_price: Decimal) -> BrokerOrderState:
        raise self._fail()

    def cancel(self, broker_order_id: str) -> BrokerOrderState:
        raise self._fail()

    def get(self, broker_order_id: str) -> BrokerOrderState:
        raise self._fail()

    def find_by_client_id(self, client_order_id: str) -> BrokerOrderState | None:
        raise self._fail()

    def profile(self) -> Any:
        raise self._fail()

    def funds(self) -> Any:
        raise self._fail()

    def positions(self) -> list[Any]:
        raise self._fail()

    def orders(self) -> list[Any]:
        raise self._fail()

    def tradebook(self) -> list[Any]:
        raise self._fail()

    def health(self) -> dict[str, Any]:
        return {"ok": False, "status": "NOT_CONNECTED", "detail": self.detail}


def active_authorization(db: Session, account: Any) -> LiveAuthorization | None:
    return db.scalar(select(LiveAuthorization).where(
        LiveAuthorization.account_id == account.id, LiveAuthorization.user_id == account.user_id,
        LiveAuthorization.revoked_at.is_(None)).order_by(LiveAuthorization.granted_at.desc())
        .limit(1))


def live_order_blockers(db: Session, settings: Settings, account: Any,
                        client: BrokerClient) -> list[str]:
    reasons = []
    if not settings.live_trading_enabled:
        reasons.append("Live trading is disabled in this build (LIVE_TRADING_ENABLED is false).")
    if account.status != "ACTIVE":
        reasons.append(f"The real account is {account.status}, not ACTIVE.")
    if active_authorization(db, account) is None:
        reasons.append("No explicit live-trading authorisation has been granted.")
    h = client.health()
    if not h.get("ok"):
        reasons.append(f"Broker is not healthy: {h.get('detail')}")
    return reasons


class RealBrokerExecutionAdapter(ExecutionAdapter):
    name = "REAL"
    account_mode = "REAL"
    supports_live_orders = True

    def __init__(self, db: Session, settings: Settings, account: Any,
                 client: BrokerClient | None = None) -> None:
        super().__init__(account)  # raises unless account.mode == "REAL"
        self.db, self.settings = db, settings
        self.client: BrokerClient = client or UnavailableBrokerClient()

    def blockers(self) -> list[str]:
        return live_order_blockers(self.db, self.settings, self.account, self.client)

    def submit(self, req: OrderRequest) -> BrokerOrderState:
        self.check_request(req)
        reasons = self.blockers()
        if reasons:
            raise LiveTradingLocked(reasons)
        return self.client.place(req)  # pragma: no cover - unreachable while locked

    def cancel(self, broker_order_id: str) -> BrokerOrderState:
        return self.client.cancel(broker_order_id)

    def modify(self, broker_order_id: str, *, quantity: int,
               limit_price: Decimal) -> BrokerOrderState:
        reasons = self.blockers()  # a modify can add risk: same guards as submit
        if reasons:
            raise LiveTradingLocked(reasons)
        return self.client.modify(  # pragma: no cover - unreachable while locked
            broker_order_id, quantity=quantity, limit_price=limit_price)

    def get_order(self, broker_order_id: str) -> BrokerOrderState:
        return self.client.get(broker_order_id)

    def find_by_client_id(self, client_order_id: str) -> BrokerOrderState | None:
        return self.client.find_by_client_id(client_order_id)

    def poll(self, now: datetime) -> int:
        return 0  # a real broker fills on its own; status is read with get_order

    def health(self) -> dict[str, Any]:
        return self.client.health()
