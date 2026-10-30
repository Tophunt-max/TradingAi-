from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base, UUIDPkMixin


class ModelVersion(UUIDPkMixin, Base):
    """An immutable trained model plus its walk-forward evaluation.

    status: CANDIDATE (evaluated), SHADOW (live predictions recorded but never
    shown or used), PROMOTED (serving), RETIRED, ROLLED_BACK, REJECTED (failed the
    gate). Only one PROMOTED version per symbol/timeframe/horizon. See
    app.learning.promotion for the acceptance criteria of each transition.
    """

    __tablename__ = "model_versions"
    __table_args__ = (Index("ix_model_versions_target", "symbol", "timeframe", "horizon_bars",
                            "status"),)

    name: Mapped[str] = mapped_column(String(80))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    horizon_bars: Mapped[int]
    algorithm: Mapped[str] = mapped_column(String(40))
    feature_version: Mapped[str] = mapped_column(String(16))
    hyperparams: Mapped[dict[str, Any]] = mapped_column(JSON)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    gate: Mapped[dict[str, Any]] = mapped_column(JSON)
    data_source: Mapped[str] = mapped_column(String(16))
    data_provider: Mapped[str] = mapped_column(String(20))
    train_rows: Mapped[int]
    data_from: Mapped[datetime]
    data_to: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(12), default="CANDIDATE")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id",
                                                                    ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    promoted_at: Mapped[datetime | None]
    retired_at: Mapped[datetime | None]
    shadow_started_at: Mapped[datetime | None]
    rolled_back_at: Mapped[datetime | None]


class Prediction(UUIDPkMixin, Base):
    """A served probability forecast. Outcomes are resolved later (Phase 7)."""

    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("model_version_id", "bar_ts",
                                       name="uq_predictions_model_bar"),)

    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    horizon_bars: Mapped[int]
    bar_ts: Mapped[datetime]
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    p_down: Mapped[float]
    p_sideways: Mapped[float]
    p_up: Mapped[float]
    regime: Mapped[str | None] = mapped_column(String(20))
    data_source: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(10))
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
