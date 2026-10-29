"""Declarative base, naming conventions and shared column types."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, MetaData, Numeric, TypeDecorator, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.time import ensure_utc, utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime that always round-trips as UTC (incl. SQLite)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        return ensure_utc(value) if value is not None else None


# Money and prices must never be floats. Use these in later phases.
Money = Numeric(18, 4)
Price = Numeric(18, 4)
Quantity = Numeric(18, 4)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: Uuid(),
        datetime: UTCDateTime(),
        Decimal: Numeric(18, 4),
    }


class UUIDPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=func.now(), onupdate=utcnow, nullable=False
    )
