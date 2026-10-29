"""Trading accounts (DEMO / REAL), the DEMO cash ledger and live-trading authorisations.

Isolation is enforced by the schema, not only by service code:
- `accounts` has UNIQUE(id, mode). Every execution row carries (account_id, account_mode)
  with a composite foreign key to accounts(id, mode), so a row can never claim a mode
  different from the account it belongs to.
- A DEMO account must have an opening balance; a REAL account must not (CHECK), so virtual
  money can never be attached to a REAL account row.
- `ledger_entries.account_mode` is CHECKed to 'DEMO' and covered by the composite FK, so a
  ledger entry can only ever belong to a DEMO account.
- `live_authorizations.account_mode` is CHECKed to 'REAL'.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, Money, UUIDPkMixin

ACCOUNT_MODES = ("DEMO", "REAL")
ACCOUNT_STATUSES = ("ACTIVE", "DISCONNECTED", "CLOSED")
DEMO_OPENING_BALANCE = Decimal("10000")
LEDGER_KINDS = ("OPENING_CREDIT", "TRADE_BUY", "TRADE_SELL", "CHARGES", "RESET_CLOSE",
                "RESET_ADJUSTMENT")
MODE_CHECK = "account_mode IN ('DEMO', 'REAL')"


def account_fk(table: str, ondelete: str = "CASCADE") -> ForeignKeyConstraint:
    """Composite FK (account_id, account_mode) -> accounts(id, mode)."""
    return ForeignKeyConstraint(["account_id", "account_mode"], ["accounts.id", "accounts.mode"],
                                name=f"fk_{table}_account", ondelete=ondelete)


class Account(UUIDPkMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "mode", name="uq_accounts_user_mode"),
        UniqueConstraint("id", "mode", name="uq_accounts_id_mode"),
        CheckConstraint("mode IN ('DEMO', 'REAL')", name="mode"),
        CheckConstraint("status IN ('ACTIVE', 'DISCONNECTED', 'CLOSED')", name="status"),
        CheckConstraint("(mode = 'DEMO' AND opening_balance IS NOT NULL) OR "
                        "(mode = 'REAL' AND opening_balance IS NULL)",
                        name="virtual_money_demo_only"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")
    # 'PAPER' for DEMO; the broker id for REAL (none is integrated in this build).
    broker: Mapped[str | None] = mapped_column(String(20))
    broker_connection_ref: Mapped[str | None] = mapped_column(String(64))
    opening_balance: Mapped[Decimal | None] = mapped_column(Money)
    reset_count: Mapped[int] = mapped_column(default=0)
    last_reset_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class LedgerEntry(UUIDPkMixin, Base):
    """DEMO cash ledger. `seq` is per account and unique, so two concurrent writers can
    never both append "the next" entry with a stale balance: one fails on the constraint."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        account_fk("ledger_entries"),
        UniqueConstraint("account_id", "seq", name="uq_ledger_entries_account_seq"),
        CheckConstraint("account_mode = 'DEMO'", name="demo_only"),
        Index("ix_ledger_entries_account_created", "account_id", "created_at"),
    )

    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4), default="DEMO")
    seq: Mapped[int]
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Money)  # signed: + credit, - debit
    balance_after: Mapped[Decimal] = mapped_column(Money)
    ref_type: Mapped[str | None] = mapped_column(String(16))  # order | fill | position | reset
    ref_id: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class LiveAuthorization(UUIDPkMixin, Base):
    """Explicit, versioned consent to trade real money through a REAL account.

    No endpoint can create one while live trading is locked in this build; the record
    exists so the REAL execution adapter can require it before every order.
    """

    __tablename__ = "live_authorizations"
    __table_args__ = (
        account_fk("live_authorizations"),
        CheckConstraint("account_mode = 'REAL'", name="real_only"),
        Index("ix_live_authorizations_account", "account_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4), default="REAL")
    consent_text_version: Mapped[str] = mapped_column(String(20))
    granted_at: Mapped[datetime] = mapped_column(default=utcnow)
    revoked_at: Mapped[datetime | None]
