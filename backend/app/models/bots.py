from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utcnow
from app.db.base import Base, Money, TimestampMixin, UUIDPkMixin


def _enum(e: type[StrEnum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class BotStatus(StrEnum):
    # Only DRAFT is reachable until the execution engine exists (Phase 5/6).
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class BotCreationMethod(StrEnum):
    AI = "AI"
    MANUAL = "MANUAL"
    TEMPLATE = "TEMPLATE"
    CLONE = "CLONE"


class TradingMode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class Bot(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "bots"
    __table_args__ = (Index("ix_bots_user_status", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    strategy_id: Mapped[str] = mapped_column(String(40))
    underlying: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    creation_method: Mapped[BotCreationMethod] = mapped_column(
        _enum(BotCreationMethod, "bot_creation_method"))
    status: Mapped[BotStatus] = mapped_column(_enum(BotStatus, "bot_status"),
                                              default=BotStatus.DRAFT)
    mode: Mapped[TradingMode] = mapped_column(_enum(TradingMode, "trading_mode"),
                                              default=TradingMode.PAPER)
    current_version: Mapped[int] = mapped_column(default=1)

    versions: Mapped[list[BotConfigVersion]] = relationship(
        back_populates="bot", cascade="all, delete-orphan", order_by="BotConfigVersion.version"
    )


class BotConfigVersion(UUIDPkMixin, Base):
    """Immutable snapshot of a bot configuration. Edits create a new version."""

    __tablename__ = "bot_config_versions"
    __table_args__ = (
        UniqueConstraint("bot_id", "version", name="uq_bot_config_versions_bot_version"),
    )

    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"),
                                              index=True)
    version: Mapped[int]
    schema_version: Mapped[int] = mapped_column(default=1)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    bot: Mapped[Bot] = relationship(back_populates="versions")


class RiskSettings(TimestampMixin, Base):
    """Per-user GLOBAL risk limits shared by every bot.

    Only the user (via the API) can change these. Strategies, bots and AI
    components must read them and can never modify them.
    """

    __tablename__ = "risk_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                               primary_key=True)
    capital: Mapped[Decimal] = mapped_column(Money, default=Decimal("10000"))
    max_risk_per_trade: Mapped[Decimal] = mapped_column(Money, default=Decimal("250"))
    daily_loss_limit: Mapped[Decimal] = mapped_column(Money, default=Decimal("800"))
    daily_profit_lock: Mapped[Decimal] = mapped_column(Money, default=Decimal("1200"))
    max_entries_per_day: Mapped[int] = mapped_column(default=2)
    max_open_positions: Mapped[int] = mapped_column(default=1)
    max_lots_per_trade: Mapped[int] = mapped_column(default=1)
    re_entry_after_stop_loss: Mapped[bool] = mapped_column(default=False)
    square_off_time_ist: Mapped[str] = mapped_column(String(5), default="15:15")
    kill_switch_engaged: Mapped[bool] = mapped_column(default=False)
    kill_switch_changed_at: Mapped[datetime | None]
