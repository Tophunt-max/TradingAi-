from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utcnow
from app.db.base import Base, Money, TimestampMixin, UUIDPkMixin
from app.models.accounts import MODE_CHECK, account_fk


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
    """Legacy execution flag kept for compatibility: PAPER maps to account mode DEMO and
    LIVE to REAL. `Bot.account_mode` is the source of truth for routing."""

    PAPER = "PAPER"
    LIVE = "LIVE"


MODE_FOR_ACCOUNT = {"DEMO": TradingMode.PAPER, "REAL": TradingMode.LIVE}


class Bot(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "bots"
    __table_args__ = (Index("ix_bots_user_status", "user_id", "status"),
                      CheckConstraint(MODE_CHECK, name="account_mode"))

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
    # Which of the user's accounts this bot trades in. Runs copy it at start.
    account_mode: Mapped[str] = mapped_column(String(4), default="DEMO", server_default="DEMO")
    current_version: Mapped[int] = mapped_column(default=1)
    status_reason: Mapped[str | None] = mapped_column(String(200))
    status_changed_at: Mapped[datetime | None]

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
    # Position policies when a limit trips (see docs/risk.md).
    exit_on_loss_limit: Mapped[bool] = mapped_column(default=True)
    square_off_on_kill_switch: Mapped[bool] = mapped_column(default=True)
    kill_switch_engaged: Mapped[bool] = mapped_column(default=False)
    kill_switch_changed_at: Mapped[datetime | None]


# ------------------------------------------------------------------ Phase 5


class BotRun(UUIDPkMixin, Base):
    """One activation of a bot, pinned to the config version it started with.

    status: RUNNING (evaluating), PAUSED, ENDED. A worker must hold the lease
    (lease_owner/lease_until) to evaluate a run, so two workers never act on
    the same bot at once.
    """

    __tablename__ = "bot_runs"
    __table_args__ = (Index("ix_bot_runs_status", "status", "lease_until"),
                      account_fk("bot_runs"), CheckConstraint(MODE_CHECK, name="account_mode"))

    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"),
                                              index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The account every order from this run goes to (fixed for the life of the run).
    account_id: Mapped[uuid.UUID]
    account_mode: Mapped[str] = mapped_column(String(4))
    config_version: Mapped[int]
    mode: Mapped[str] = mapped_column(String(16), default="SIGNAL_ONLY")
    status: Mapped[str] = mapped_column(String(10), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[datetime | None]
    end_reason: Mapped[str | None] = mapped_column(String(200))
    last_heartbeat_at: Mapped[datetime | None]
    last_bar_ts: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_error_at: Mapped[datetime | None]
    consecutive_errors: Mapped[int] = mapped_column(default=0)
    signals_count: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None]


class BotSignal(UUIDPkMixin, Base):
    """An entry signal produced by a running bot. No order is placed in SIGNAL_ONLY mode."""

    __tablename__ = "bot_signals"
    __table_args__ = (
        UniqueConstraint("run_id", "bar_ts", name="uq_bot_signals_run_bar"),
        Index("ix_bot_signals_user_bar", "user_id", "bar_ts"),
    )

    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"),
                                              index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    config_version: Mapped[int]
    bar_ts: Mapped[datetime]
    side: Mapped[str] = mapped_column(String(2))
    underlying: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    price: Mapped[Decimal] = mapped_column(Money)
    regime: Mapped[str | None] = mapped_column(String(20))
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    data_source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class BotEvent(UUIDPkMixin, Base):
    """Bot log: state changes, signals, suppressed signals, errors."""

    __tablename__ = "bot_events"
    __table_args__ = (Index("ix_bot_events_bot_ts", "bot_id", "ts"),)

    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bot_runs.id",
                                                                ondelete="SET NULL"))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    level: Mapped[str] = mapped_column(String(8))  # INFO | WARN | ERROR
    kind: Mapped[str] = mapped_column(String(16))  # STATE | SIGNAL | SUPPRESSED | ERROR
    message: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    instance: Mapped[str] = mapped_column(String(80))
    last_seen: Mapped[datetime]
    info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
