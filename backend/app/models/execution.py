"""Orders, fills, positions, daily risk state and the paper broker's own ledger."""

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
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, Money, Price, UUIDPkMixin
from app.models.accounts import MODE_CHECK, account_fk

ACTIVE_ORDER_STATES = ("CREATED", "VALIDATED", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED",
                       "CANCEL_PENDING", "UNKNOWN", "RECONCILIATION_REQUIRED")
TERMINAL_ORDER_STATES = ("FILLED", "REJECTED", "CANCELLED")


class Order(UUIDPkMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_account_status", "account_id", "status"),
        account_fk("orders"),
        CheckConstraint(MODE_CHECK, name="account_mode"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Every execution row belongs to exactly one account; (account_id, account_mode) is a
    # composite FK to accounts(id, mode), so the mode can never disagree with the account.
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4))
    bot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bots.id", ondelete="SET NULL"))
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bot_signals.id", ondelete="SET NULL"))
    # positions.entry_order_id points back at orders, so this FK is added after both tables.
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("positions.id", ondelete="SET NULL", use_alter=True,
                   name="fk_orders_position_id_positions"))
    client_order_id: Mapped[str] = mapped_column(String(64))
    broker: Mapped[str] = mapped_column(String(20), default="PAPER")
    broker_order_id: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(8), default="PAPER")
    purpose: Mapped[str] = mapped_column(String(8))  # ENTRY | EXIT
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    underlying: Mapped[str] = mapped_column(String(32))
    trading_symbol: Mapped[str] = mapped_column(String(64))
    security_id: Mapped[str | None] = mapped_column(String(32))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[Decimal] = mapped_column(Price)
    option_type: Mapped[str] = mapped_column(String(2))
    lot_size: Mapped[int]
    lot_size_source: Mapped[str] = mapped_column(String(24))
    quantity: Mapped[int]
    order_type: Mapped[str] = mapped_column(String(8))
    limit_price: Mapped[Decimal | None] = mapped_column(Price)
    stop_price: Mapped[Decimal | None] = mapped_column(Price)
    target_price: Mapped[Decimal | None] = mapped_column(Price)
    trailing_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(24), default="CREATED")
    filled_qty: Mapped[int] = mapped_column(default=0)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Price)
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    exit_reason: Mapped[str | None] = mapped_column(String(24))
    data_source: Mapped[str] = mapped_column(String(16))
    risk_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None]
    terminal_at: Mapped[datetime | None]


class OrderEvent(UUIDPkMixin, Base):
    __tablename__ = "order_events"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"),
                                                index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(String(500))


class Fill(UUIDPkMixin, Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("broker", "broker_fill_id", name="uq_fills_broker_fill"),
                      account_fk("fills"), CheckConstraint(MODE_CHECK, name="account_mode"))

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"),
                                                index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Every execution row belongs to exactly one account; (account_id, account_mode) is a
    # composite FK to accounts(id, mode), so the mode can never disagree with the account.
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4))
    broker: Mapped[str] = mapped_column(String(20))
    broker_fill_id: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Price)
    ts: Mapped[datetime]
    charges: Mapped[dict[str, Any]] = mapped_column(JSON)
    charges_total: Mapped[Decimal] = mapped_column(Money)


class Position(UUIDPkMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (Index("ix_positions_user_status", "user_id", "status"),
                      Index("ix_positions_account_status", "account_id", "status"),
                      account_fk("positions"), CheckConstraint(MODE_CHECK, name="account_mode"))

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Every execution row belongs to exactly one account; (account_id, account_mode) is a
    # composite FK to accounts(id, mode), so the mode can never disagree with the account.
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4))
    bot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bots.id", ondelete="SET NULL"))
    entry_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id",
                                                                 ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(8), default="PAPER")
    underlying: Mapped[str] = mapped_column(String(32))
    trading_symbol: Mapped[str] = mapped_column(String(64))
    security_id: Mapped[str | None] = mapped_column(String(32))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[Decimal] = mapped_column(Price)
    option_type: Mapped[str] = mapped_column(String(2))
    lot_size: Mapped[int]
    quantity: Mapped[int]  # currently open units
    entry_qty: Mapped[int]
    exit_qty: Mapped[int] = mapped_column(default=0)
    avg_entry: Mapped[Decimal] = mapped_column(Price)
    avg_exit: Mapped[Decimal | None] = mapped_column(Price)
    stop_price: Mapped[Decimal | None] = mapped_column(Price)
    target_price: Mapped[Decimal | None] = mapped_column(Price)
    trailing_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    high_water: Mapped[Decimal | None] = mapped_column(Price)
    last_price: Mapped[Decimal | None] = mapped_column(Price)
    last_price_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(10), default="OPEN")  # OPEN | CLOSING | CLOSED
    exit_reason: Mapped[str | None] = mapped_column(String(24))
    realized_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    charges_total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    data_source: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None]

    @property
    def realized_net(self) -> Decimal:
        return self.realized_gross - self.charges_total


class DailyRiskState(UUIDPkMixin, Base):
    """Per-account, per-IST-day risk counters. Persisted, so limits survive restarts.

    Limits (RiskSettings) are a per-user policy; usage is counted separately per account,
    so DEMO activity never consumes REAL limits and vice versa."""

    __tablename__ = "daily_risk_state"
    __table_args__ = (
        UniqueConstraint("account_id", "trading_day", name="uq_daily_risk_account_day"),
        account_fk("daily_risk_state"), CheckConstraint(MODE_CHECK, name="account_mode"))

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Every execution row belongs to exactly one account; (account_id, account_mode) is a
    # composite FK to accounts(id, mode), so the mode can never disagree with the account.
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4))
    trading_day: Mapped[date] = mapped_column(Date)
    realized_gross: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    charges: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    entries: Mapped[int] = mapped_column(default=0)
    stop_loss_exits: Mapped[int] = mapped_column(default=0)
    loss_limit_hit_at: Mapped[datetime | None]
    profit_lock_hit_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    @property
    def realized_net(self) -> Decimal:
        return self.realized_gross - self.charges


class WorkerLease(Base):
    """Singleton work (e.g. the execution pass) is done by whichever worker holds the lease."""

    __tablename__ = "worker_leases"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(80))
    lease_until: Mapped[datetime]


# ---------------------------------------------------- paper broker's own books


class PaperBrokerOrder(Base):
    """The simulated broker's ledger, kept apart from our orders table so that
    reconciliation works exactly as it will against a real broker."""

    __tablename__ = "paper_broker_orders"
    __table_args__ = (UniqueConstraint("client_order_id", name="uq_paper_orders_client_id"),
                      Index("ix_paper_broker_orders_account_status", "account_id", "status"),
                      account_fk("paper_broker_orders"),
                      CheckConstraint("account_mode = 'DEMO'", name="demo_only"))

    broker_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The simulator only ever holds DEMO orders (CHECK + composite FK).
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4), default="DEMO")
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int]
    order_type: Mapped[str] = mapped_column(String(8))
    limit_price: Mapped[Decimal | None] = mapped_column(Price)
    contract: Mapped[dict[str, Any]] = mapped_column(JSON)
    # OPEN | PARTIAL | FILLED | CANCELLED | REJECTED
    status: Mapped[str] = mapped_column(String(10))
    filled_qty: Mapped[int] = mapped_column(default=0)
    avg_price: Mapped[Decimal | None] = mapped_column(Price)
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class PaperBrokerFill(Base):
    __tablename__ = "paper_broker_fills"
    __table_args__ = (account_fk("paper_broker_fills"),
                      CheckConstraint("account_mode = 'DEMO'", name="demo_only"))

    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(
        ForeignKey("paper_broker_orders.broker_order_id", ondelete="CASCADE"), index=True)
    # The simulator only ever holds DEMO orders (CHECK + composite FK).
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4), default="DEMO")
    quantity: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Price)
    ts: Mapped[datetime]
