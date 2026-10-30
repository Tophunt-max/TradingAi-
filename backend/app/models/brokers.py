"""Broker connections, broker data snapshots and charges reconciliation (REAL accounts only).

- `broker_connections`: one row per user and broker. The access token is stored only as
  ciphertext (`token_ciphertext`, Fernet); it is cleared on disconnect. CHECKed to REAL and
  bound to the REAL account by the composite FK (account_id, account_mode).
- `broker_snapshots`: what the broker reported at `fetched_at` (FUNDS, POSITIONS, ORDERS,
  FILLS), as JSON. REAL balances shown in the app come from the latest FUNDS snapshot.
- `broker_charge_reconciliations`: broker-reported charges for a day vs our estimate.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, Money, UUIDPkMixin
from app.models.accounts import account_fk

CONNECTION_STATUSES = ("CONNECTED", "EXPIRED", "REVOKED", "ERROR")
SNAPSHOT_KINDS = ("FUNDS", "POSITIONS", "ORDERS", "FILLS")


class BrokerConnection(UUIDPkMixin, Base):
    __tablename__ = "broker_connections"
    __table_args__ = (
        account_fk("broker_connections"),
        UniqueConstraint("user_id", "broker", name="uq_broker_connections_user_broker"),
        CheckConstraint("account_mode = 'REAL'", name="real_only"),
        CheckConstraint("status IN ('CONNECTED', 'EXPIRED', 'REVOKED', 'ERROR')", name="status"),
        Index("ix_broker_connections_account", "account_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    broker: Mapped[str] = mapped_column(String(20))
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4), default="REAL")
    auth_flow: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(10))
    # Fernet ciphertext; never the raw token. NULL after disconnect.
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None]
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_sync_at: Mapped[datetime | None]
    last_checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    revoked_at: Mapped[datetime | None]


class BrokerSnapshot(UUIDPkMixin, Base):
    __tablename__ = "broker_snapshots"
    __table_args__ = (
        account_fk("broker_snapshots"),
        CheckConstraint("account_mode = 'REAL'", name="real_only"),
        CheckConstraint("kind IN ('FUNDS', 'POSITIONS', 'ORDERS', 'FILLS')", name="kind"),
        Index("ix_broker_snapshots_account_kind_fetched", "account_id", "kind", "fetched_at"),
    )

    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="SET NULL"))
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4), default="REAL")
    broker: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(10))
    payload: Mapped[Any] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class BrokerChargeReconciliation(UUIDPkMixin, Base):
    """Broker-reported charges for one IST day vs the sum of our per-fill estimates."""

    __tablename__ = "broker_charge_reconciliations"
    __table_args__ = (
        account_fk("broker_charge_reconciliations"),
        UniqueConstraint("account_id", "trading_day", name="uq_broker_charge_recon_account_day"),
        CheckConstraint("account_mode = 'REAL'", name="real_only"),
    )

    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4), default="REAL")
    broker: Mapped[str] = mapped_column(String(20))
    trading_day: Mapped[date] = mapped_column(Date)
    broker_reported: Mapped[Decimal] = mapped_column(Money)
    estimated: Mapped[Decimal] = mapped_column(Money)
    diff: Mapped[Decimal] = mapped_column(Money)  # broker_reported - estimated
    fills: Mapped[int] = mapped_column(default=0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
