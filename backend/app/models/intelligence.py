"""Fundamentals, market events and self-learning records (work packages G, H, N).

Every externally sourced row stores when it became public (`published_at`) and
when this system received it (`available_at`). Point-in-time queries filter on
both, so a historical decision can only see what was knowable at that moment.
Rows are never overwritten: restatements and event updates are new rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, Price, UUIDPkMixin

# Wide enough for company-level amounts in rupees (up to 10^20).
Amount = Numeric(24, 4)

FUNDAMENTAL_LINE_ITEMS = (
    "revenue", "gross_profit", "operating_income", "net_income", "eps",
    "operating_cash_flow", "capex", "depreciation_amortization", "total_debt",
    "interest_expense", "equity", "capital_employed", "total_assets", "current_assets",
    "current_liabilities", "shares_outstanding", "dividends_per_share", "cash",
)


class FundamentalReport(UUIDPkMixin, Base):
    """One version of one period's statement. Restatements get a higher `revision`."""

    __tablename__ = "fundamental_reports"
    __table_args__ = (
        UniqueConstraint("symbol", "period_end", "period_type", "value_basis", "revision",
                         name="uq_fundamental_reports_version"),
        Index("ix_fundamental_reports_symbol_available", "symbol", "available_at"),
    )

    symbol: Mapped[str] = mapped_column(String(32))
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(2))  # Q | FY
    value_basis: Mapped[str] = mapped_column(String(10))  # REPORTED|ESTIMATED|ADJUSTED|RESTATED
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    revenue: Mapped[Decimal | None] = mapped_column(Amount)
    gross_profit: Mapped[Decimal | None] = mapped_column(Amount)
    operating_income: Mapped[Decimal | None] = mapped_column(Amount)
    net_income: Mapped[Decimal | None] = mapped_column(Amount)
    eps: Mapped[Decimal | None] = mapped_column(Amount)
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Amount)
    capex: Mapped[Decimal | None] = mapped_column(Amount)
    depreciation_amortization: Mapped[Decimal | None] = mapped_column(Amount)
    total_debt: Mapped[Decimal | None] = mapped_column(Amount)
    interest_expense: Mapped[Decimal | None] = mapped_column(Amount)
    equity: Mapped[Decimal | None] = mapped_column(Amount)
    capital_employed: Mapped[Decimal | None] = mapped_column(Amount)
    total_assets: Mapped[Decimal | None] = mapped_column(Amount)
    current_assets: Mapped[Decimal | None] = mapped_column(Amount)
    current_liabilities: Mapped[Decimal | None] = mapped_column(Amount)
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Amount)
    dividends_per_share: Mapped[Decimal | None] = mapped_column(Amount)
    cash: Mapped[Decimal | None] = mapped_column(Amount)
    source: Mapped[str] = mapped_column(String(80))
    source_ref: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime]
    available_at: Mapped[datetime]
    revision: Mapped[int] = mapped_column(default=0)
    quality_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class CompanyProfile(UUIDPkMixin, Base):
    """Sector/peer classification. Indices may list constituents with weights."""

    __tablename__ = "company_profiles"
    __table_args__ = (UniqueConstraint("symbol", name="uq_company_profiles_symbol"),)

    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(120))
    sector: Mapped[str | None] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(80))
    peer_group: Mapped[str | None] = mapped_column(String(80))
    is_index: Mapped[bool] = mapped_column(default=False)
    constituents: Mapped[list[Any] | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(80))
    available_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class CorporateAction(UUIDPkMixin, Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (UniqueConstraint("symbol", "action_type", "ex_date", "source",
                                       name="uq_corporate_actions_identity"),
                      Index("ix_corporate_actions_symbol_ex", "symbol", "ex_date"))

    symbol: Mapped[str] = mapped_column(String(32))
    action_type: Mapped[str] = mapped_column(String(10))  # SPLIT|BONUS|DIVIDEND|RIGHTS
    ex_date: Mapped[date] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    ratio_from: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ratio_to: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    amount: Mapped[Decimal | None] = mapped_column(Price)
    announced_at: Mapped[datetime]
    available_at: Mapped[datetime]
    source: Mapped[str] = mapped_column(String(80))
    source_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class MarketEvent(UUIDPkMixin, Base):
    """An imported event or announcement. Never generated from sentiment."""

    __tablename__ = "market_events"
    __table_args__ = (Index("ix_market_events_event_time", "event_time"),
                      Index("ix_market_events_fingerprint", "fingerprint"),
                      Index("ix_market_events_available", "available_at"))

    category: Mapped[str] = mapped_column(String(24))
    scope: Mapped[str] = mapped_column(String(12))  # INSTRUMENT | SECTOR | INDEX | MARKET
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(80))
    source_reliability: Mapped[str] = mapped_column(String(16))
    url: Mapped[str | None] = mapped_column(String(500))
    event_time: Mapped[datetime]
    published_at: Mapped[datetime]
    available_at: Mapped[datetime]
    expected_value: Mapped[Decimal | None] = mapped_column(Amount)
    actual_value: Mapped[Decimal | None] = mapped_column(Amount)
    unit: Mapped[str | None] = mapped_column(String(16))
    novelty: Mapped[str] = mapped_column(String(10))  # NEW | UPDATE | DUPLICATE
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_events.id", ondelete="SET NULL"))
    importance: Mapped[str] = mapped_column(String(6))  # LOW | MEDIUM | HIGH
    direction_hint: Mapped[str | None] = mapped_column(String(8))
    uncertainty_note: Mapped[str | None] = mapped_column(String(300))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class DecisionRecord(UUIDPkMixin, Base):
    """Every analysis or bot decision, including NO TRADE and skipped ones.

    At most one row per (symbol, timeframe, bar, origin, origin_ref): `origin` is
    ANALYSIS or BOT, `origin_ref` is '' for analysis and the bot id for bots.
    Outcome fields are filled only after the horizon has elapsed.
    """

    __tablename__ = "decision_records"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "bar_ts", "origin", "origin_ref",
                         name="uq_decision_records_bar"),
        Index("ix_decision_records_status", "outcome_status", "decision_ts"),
        Index("ix_decision_records_symbol_ts", "symbol", "decision_ts"),
    )

    origin: Mapped[str] = mapped_column(String(10))
    origin_ref: Mapped[str] = mapped_column(String(40), default="")
    decision_ts: Mapped[datetime]
    bar_ts: Mapped[datetime]
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    horizon_bars: Mapped[int]
    close: Mapped[Decimal] = mapped_column(Price)
    atr: Mapped[float | None]
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"))
    model_name: Mapped[str | None] = mapped_column(String(80))
    feature_version: Mapped[str | None] = mapped_column(String(16))
    indicator_version: Mapped[str | None] = mapped_column(String(16))
    data_quality: Mapped[str] = mapped_column(String(8))
    data_source: Mapped[str] = mapped_column(String(16))
    regime: Mapped[str | None] = mapped_column(String(24))
    regime_probabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    probabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    uncertainty: Mapped[float | None]
    calibration_status: Mapped[str] = mapped_column(String(16))
    predicted_direction: Mapped[str | None] = mapped_column(String(8))
    predicted_vol_range: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(20))
    no_trade_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    strategies: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    event_risk: Mapped[str | None] = mapped_column(String(10))
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"))
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("positions.id", ondelete="SET NULL"))
    account_mode: Mapped[str | None] = mapped_column(String(4))
    # Outcome (filled by app.learning.outcomes.resolve_outcomes).
    outcome_status: Mapped[str] = mapped_column(String(12), default="PENDING")
    resolved_at: Mapped[datetime | None]
    realized_return: Mapped[float | None]
    realized_direction: Mapped[str | None] = mapped_column(String(8))
    realized_volatility: Mapped[float | None]
    max_favorable_excursion: Mapped[float | None]
    max_adverse_excursion: Mapped[float | None]
    regime_at_resolution: Mapped[str | None] = mapped_column(String(24))
    option_premium_change: Mapped[Decimal | None] = mapped_column(Price)
    slippage: Mapped[Decimal | None] = mapped_column(Price)
    error_classes: Mapped[list[str]] = mapped_column(JSON, default=list)
    outcome_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class ModelDriftReport(UUIDPkMixin, Base):
    """Feature, prediction and performance drift of one model at one time."""

    __tablename__ = "model_drift_reports"
    __table_args__ = (Index("ix_model_drift_reports_model_ts", "model_version_id",
                            "computed_at"),)

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"))
    computed_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(20))  # STABLE|MODERATE|DEGRADED|INSUFFICIENT_DATA
    reference_basis: Mapped[str] = mapped_column(String(32))
    window_rows: Mapped[int]
    max_feature_psi: Mapped[float | None]
    feature_psi: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prediction_psi: Mapped[float | None]
    performance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
