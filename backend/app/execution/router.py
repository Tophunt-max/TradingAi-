"""Execution router: picks the adapter for an account.

The choice is made only from the account row loaded server-side (by id, from the order,
position, bot run or the user's own account), never from client input. DEMO -> paper
simulator; REAL -> the real-broker adapter (bound to the account's broker connection),
which is locked in this build. Both adapters
also refuse accounts of the other mode, so a wrong route fails loudly instead of trading.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.brokers.base import ExecutionAdapter
from app.brokers.client import BrokerClient
from app.brokers.paper import PaperExecutionAdapter
from app.brokers.real import RealBrokerExecutionAdapter
from app.core.config import Settings
from app.models.accounts import Account

QuoteFn = Callable[[dict[str, Any]], dict[str, Any] | None]


def _no_quote(_: dict[str, Any]) -> dict[str, Any] | None:
    return None


def real_broker_client(db: Session, settings: Settings, account: Account) -> BrokerClient:
    """The client bound to the REAL account's broker connection, or a client that fails
    every call when there is no usable connection (app/brokers/connections.py)."""
    from app.brokers.connections import real_broker_client as for_account

    return for_account(db, settings, account)


def route_adapter(db: Session, settings: Settings, account: Account,
                  quote_fn: QuoteFn | None = None) -> ExecutionAdapter:
    if account.mode == "DEMO":
        return PaperExecutionAdapter(
            db, account, quote_fn or _no_quote, slippage_ticks=settings.paper_slippage_ticks,
            max_spread_pct=settings.paper_max_spread_pct,
            max_fill_qty_per_pass=settings.paper_max_fill_qty_per_pass)
    if account.mode == "REAL":
        return RealBrokerExecutionAdapter(db, settings, account,
                                          real_broker_client(db, settings, account))
    raise ValueError(f"Unknown account mode {account.mode!r}.")


def load_account(db: Session, account_id: uuid.UUID) -> Account:
    acc = db.get(Account, account_id)
    if acc is None:
        raise LookupError(f"Account {account_id} not found.")
    return acc
