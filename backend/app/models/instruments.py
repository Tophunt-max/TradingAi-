from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, Price, UUIDPkMixin


class Instrument(UUIDPkMixin, Base):
    """Row of the broker's instrument master (scrip master).

    Lot size, tick size, expiry and strike come from here and only from here.
    The table is replaced per `source` on every sync.
    """

    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "source",
            "exchange",
            "security_id",
            name="uq_instruments_provider_source_exch_secid",
        ),
        Index("ix_instruments_symbol", "symbol_name", "instrument_name", "expiry_date"),
        Index("ix_instruments_trading_symbol", "trading_symbol"),
    )

    provider: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(10))  # equity | fno | index
    exchange: Mapped[str] = mapped_column(String(8))
    segment: Mapped[str | None] = mapped_column(String(40))
    security_id: Mapped[str] = mapped_column(String(20))
    instrument_name: Mapped[str | None] = mapped_column(String(20))  # EQUITY, OPTIDX, FUTIDX...
    trading_symbol: Mapped[str | None] = mapped_column(String(60))
    symbol_name: Mapped[str | None] = mapped_column(String(60))
    display_name: Mapped[str | None] = mapped_column(String(120))
    series: Mapped[str | None] = mapped_column(String(10))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    strike_price: Mapped[Decimal | None] = mapped_column(Price)
    option_type: Mapped[str | None] = mapped_column(String(2))
    lot_size: Mapped[int | None]
    tick_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    freeze_qty: Mapped[int | None]
    isin: Mapped[str | None] = mapped_column(String(12))
    synced_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class InstrumentSync(UUIDPkMixin, Base):
    """Audit of instrument-master downloads."""

    __tablename__ = "instrument_syncs"

    provider: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))  # SUCCESS | FAILED
    row_count: Mapped[int] = mapped_column(default=0)
    rejected_rows: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
