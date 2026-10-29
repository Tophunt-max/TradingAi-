"""Request models for /api/v1/backtests."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.bots import Condition, Timeframe
from app.schemas.common import ApiModel

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

MAX_BARS = 2000
MAX_SENSITIVITY_CELLS = 36
MAX_WF_GRID = 16
MAX_WF_WINDOWS = 8
MAX_SIMULATIONS = 5000


class LevelIn(ApiModel):
    type: Literal["PERCENT", "POINTS"] = "PERCENT"
    value: float = Field(gt=0, le=5000)

    @model_validator(mode="after")
    def _pct(self) -> LevelIn:
        if self.type == "PERCENT" and self.value > 20:
            raise ValueError("A percentage level on the underlying must be at most 20%.")
        return self


class CostsIn(ApiModel):
    spread_bps: float = Field(default=2.0, ge=0, le=200)
    slippage_bps: float = Field(default=0.0, ge=0, le=100)
    slippage_ticks: int = Field(default=1, ge=0, le=20)
    tick_size: float = Field(default=0.05, gt=0, le=10)
    brokerage_per_order: float | None = Field(default=None, ge=0, le=100)
    charge_premium: float | None = Field(default=None, gt=0, le=100_000)
    charge_premium_pct: float = Field(default=0.6, gt=0, le=10)
    reject_probability: float = Field(default=0.0, ge=0, le=0.5)
    partial_fill_probability: float = Field(default=0.0, ge=0, le=1)
    min_volume: float = Field(default=0.0, ge=0)
    latency_bars: int = Field(default=0, ge=0, le=5)
    entry_fill: Literal["NEXT_OPEN", "NEXT_BAR_WORST"] = "NEXT_OPEN"
    stop_fill: Literal["STOP_PRICE", "BAR_WORST"] = "STOP_PRICE"


class RiskIn(ApiModel):
    """Overrides may only be stricter than the user's global risk limits."""

    risk_per_trade: float | None = Field(default=None, gt=0)
    daily_loss_limit: float | None = Field(default=None, gt=0)
    daily_profit_lock: float | None = Field(default=None, gt=0)
    max_entries_per_day: int | None = Field(default=None, ge=1, le=20)
    lots: int | None = Field(default=None, ge=1, le=50)
    re_entry_after_stop_loss: bool | None = None
    square_off_time_ist: str | None = None
    session_start_ist: str | None = None
    session_end_ist: str | None = None
    cooldown_minutes: int | None = Field(default=None, ge=0, le=240)

    @field_validator("square_off_time_ist", "session_start_ist", "session_end_ist")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        if v is not None and not _HHMM.match(v):
            raise ValueError("Use HH:MM (24h).")
        return v


class BacktestRequest(ApiModel):
    strategy_id: str | None = None
    bot_id: uuid.UUID | None = None
    underlying: str | None = Field(default=None, max_length=32)
    timeframe: Timeframe | None = None
    side: Literal["CE", "PE"] | None = None
    bars: int = Field(default=1000, ge=150, le=MAX_BARS)
    fidelity: Literal["UNDERLYING_PROXY", "HISTORICAL_OPTIONS"] = "UNDERLYING_PROXY"
    entry_logic: Literal["ALL", "ANY"] = "ALL"
    entry_conditions: list[Condition] | None = Field(default=None, max_length=8)
    threshold: float | None = None
    stop: LevelIn = Field(default_factory=lambda: LevelIn(value=0.3))
    target: LevelIn = Field(default_factory=lambda: LevelIn(value=0.6))
    lot_size: int = Field(default=1, ge=1, le=5000)
    costs: CostsIn = Field(default_factory=CostsIn)
    risk: RiskIn = Field(default_factory=RiskIn)
    regime_filter: bool = False
    compare_timeframes: list[Timeframe] = Field(default_factory=list, max_length=2)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _source(self) -> BacktestRequest:
        if (self.strategy_id is None) == (self.bot_id is None):
            raise ValueError("Give exactly one of strategy_id or bot_id.")
        if self.bot_id is not None and any(
                v is not None for v in (self.underlying, self.timeframe, self.side,
                                        self.entry_conditions)):
            raise ValueError("With bot_id, underlying, timeframe, side and entry conditions "
                             "come from the bot's configuration.")
        return self


class GridIn(ApiModel):
    stops_pct: list[float] = Field(default_factory=lambda: [0.2, 0.3, 0.5], min_length=1,
                                   max_length=6)
    targets_pct: list[float] = Field(default_factory=lambda: [0.4, 0.6, 1.0], min_length=1,
                                     max_length=6)
    thresholds: list[float] | None = Field(default=None, max_length=4)

    @field_validator("stops_pct", "targets_pct")
    @classmethod
    def _range(cls, v: list[float]) -> list[float]:
        if any(not (0 < x <= 20) for x in v):
            raise ValueError("Grid levels must be between 0 and 20%.")
        return sorted(set(v))

    @property
    def size(self) -> int:
        return len(self.stops_pct) * len(self.targets_pct) * len(self.thresholds or [None])


class WalkForwardRequest(ApiModel):
    run: BacktestRequest
    grid: GridIn = Field(default_factory=lambda: GridIn(stops_pct=[0.2, 0.4],
                                                        targets_pct=[0.4, 0.8]))
    train_bars: int = Field(default=400, ge=150, le=1500)
    test_bars: int = Field(default=150, ge=50, le=1000)
    holdout_pct: float = Field(default=20, ge=0, le=40)
    objective: Literal["net_pnl", "profit_factor", "expectancy", "sharpe"] = "net_pnl"
    min_trades: int = Field(default=3, ge=1, le=50)
    max_windows: int = Field(default=6, ge=1, le=MAX_WF_WINDOWS)

    @model_validator(mode="after")
    def _cap(self) -> WalkForwardRequest:
        if self.grid.size > MAX_WF_GRID:
            raise ValueError(f"Walk-forward grids are limited to {MAX_WF_GRID} combinations.")
        return self


class SensitivityRequest(ApiModel):
    run: BacktestRequest
    grid: GridIn = Field(default_factory=GridIn)

    @model_validator(mode="after")
    def _cap(self) -> SensitivityRequest:
        if self.grid.size > MAX_SENSITIVITY_CELLS:
            raise ValueError(f"Sensitivity grids are limited to {MAX_SENSITIVITY_CELLS} cells.")
        return self


class MonteCarloRequest(ApiModel):
    run: BacktestRequest
    simulations: int = Field(default=1000, ge=100, le=MAX_SIMULATIONS)
    seed: int = Field(default=7, ge=0, le=2**31 - 1)
