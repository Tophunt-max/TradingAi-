from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.models.bots import BotCreationMethod, BotStatus, TradingMode
from app.schemas.common import ApiModel
from app.strategies.catalog import STRATEGIES

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
Timeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h"]

# Initial live-execution scope: NIFTY / BANKNIFTY options buying.
SUPPORTED_OPTION_UNDERLYINGS = {"NIFTY", "BANKNIFTY"}


class InstrumentSpec(ApiModel):
    category: Literal["INDEX_OPTION"] = "INDEX_OPTION"
    exchange: Literal["NSE"] = "NSE"
    underlying: str
    option_type: Literal["CE", "PE"]
    strike_selection: Literal["ATM", "ITM_1", "OTM_1", "OTM_2"] = "ATM"
    expiry_selection: Literal["NEAREST", "NEXT"] = "NEAREST"
    side: Literal["BUY"] = "BUY"  # options buying only; selling is out of scope

    @field_validator("underlying")
    @classmethod
    def _u(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_OPTION_UNDERLYINGS:
            raise ValueError("Only NIFTY and BANKNIFTY options are supported in this phase.")
        return v


class Condition(ApiModel):
    indicator: str = Field(min_length=2, max_length=40, pattern=r"^[A-Z0-9_]+$")
    params: dict[str, Any] = Field(default_factory=dict)
    operator: Literal["GT", "LT", "CROSS_ABOVE", "CROSS_BELOW", "PRICE_ABOVE", "PRICE_BELOW",
                      "CLOSE_ABOVE", "CLOSE_BELOW", "PRESENT"]
    value: float | None = None


class EntrySpec(ApiModel):
    logic: Literal["ALL", "ANY"] = "ALL"
    conditions: list[Condition] = Field(default_factory=list, max_length=8)


class Level(ApiModel):
    type: Literal["PERCENT", "POINTS"]
    value: Decimal = Field(gt=0, le=Decimal("1000"))


class ExitSpec(ApiModel):
    stop_loss: Level
    target: Level
    trailing_stop: Level | None = None
    square_off_time_ist: str = "15:15"

    @field_validator("square_off_time_ist")
    @classmethod
    def _t(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("Use HH:MM (24h).")
        return v

    @model_validator(mode="after")
    def _sl_required(self) -> ExitSpec:
        if self.stop_loss.type == "PERCENT" and self.stop_loss.value >= 100:
            raise ValueError("A percentage stop-loss must be below 100%.")
        return self


class OrderSpec(ApiModel):
    order_type: Literal["MARKET", "LIMIT"] = "LIMIT"
    limit_offset_pct: Decimal = Field(default=Decimal("0.5"), ge=0, le=5)


class RiskSpec(ApiModel):
    capital_allocation: Decimal = Field(gt=0, le=Decimal("10000000"))
    risk_per_trade: Decimal = Field(gt=0)
    max_entries_per_day: int = Field(ge=1, le=20)
    max_open_positions: int = Field(ge=1, le=5)
    re_entry_after_stop_loss: bool = False


class SessionSpec(ApiModel):
    start_ist: str = "09:20"
    end_ist: str = "15:00"
    cooldown_minutes: int = Field(default=15, ge=0, le=240)

    @field_validator("start_ist", "end_ist")
    @classmethod
    def _t(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("Use HH:MM (24h).")
        return v

    @model_validator(mode="after")
    def _order(self) -> SessionSpec:
        if self.start_ist >= self.end_ist:
            raise ValueError("Session start must be before session end.")
        return self


class FilterSpec(ApiModel):
    min_relative_volume: float | None = Field(default=None, ge=0, le=20)
    max_atr_pct: float | None = Field(default=None, ge=0, le=50)
    news_filter: bool = False


class NotificationSpec(ApiModel):
    signals: bool = True
    orders: bool = True
    risk: bool = True


class BotConfig(ApiModel):
    """Versioned, schema-validated bot configuration (schema_version 1)."""

    name: str = Field(min_length=3, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    strategy_id: str
    timeframe: Timeframe
    instrument: InstrumentSpec
    entry: EntrySpec = Field(default_factory=EntrySpec)
    exit: ExitSpec
    order: OrderSpec = Field(default_factory=OrderSpec)
    risk: RiskSpec
    session: SessionSpec = Field(default_factory=SessionSpec)
    filters: FilterSpec = Field(default_factory=FilterSpec)
    notifications: NotificationSpec = Field(default_factory=NotificationSpec)
    mode: Literal["PAPER"] = "PAPER"

    @field_validator("strategy_id")
    @classmethod
    def _strategy(cls, v: str) -> str:
        if v not in STRATEGIES:
            raise ValueError(f"Unknown strategy '{v}'.")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> BotConfig:
        if self.risk.risk_per_trade > self.risk.capital_allocation:
            raise ValueError("Risk per trade cannot exceed the capital allocation.")
        if self.exit.square_off_time_ist <= self.session.start_ist:
            raise ValueError("Square-off time must be after the session start.")
        return self


class BotCreate(ApiModel):
    creation_method: Literal["AI", "MANUAL", "TEMPLATE"]
    config: BotConfig


class BotUpdate(ApiModel):
    config: BotConfig


class BotOut(ApiModel):
    id: uuid.UUID
    name: str
    strategy_id: str
    strategy_name: str
    underlying: str
    timeframe: str
    creation_method: BotCreationMethod
    status: BotStatus
    mode: TradingMode
    current_version: int
    created_at: datetime
    updated_at: datetime
    # Execution metrics stay null until the execution engine exists.
    today_pnl: Decimal | None = None
    total_net_pnl: Decimal | None = None
    trade_count: int = 0
    last_signal: str | None = None
    health: str = "NOT_RUNNING"


class BotDetail(BotOut):
    config: BotConfig
    versions: list[int]


class ValidationCheck(ApiModel):
    key: str
    label: str
    status: Literal["PASS", "FAIL", "WARN"]
    message: str


class ValidationReport(ApiModel):
    bot_id: uuid.UUID
    version: int
    can_activate: bool
    checks: list[ValidationCheck]


class AiDraftRequest(ApiModel):
    underlying: Literal["NIFTY", "BANKNIFTY"]
    style: Literal["AUTO", "MOMENTUM", "SCALPING", "BREAKOUT", "REVERSAL", "SMC"]
    timeframe: Timeframe = "5m"
    capital: Decimal = Field(gt=0, le=Decimal("10000000"))
    risk_profile: Literal["CONSERVATIVE", "MODERATE", "AGGRESSIVE"] = "CONSERVATIVE"
    option_type: Literal["CE", "PE"] = "CE"


class AiDraftResponse(ApiModel):
    generator: str
    config: BotConfig
    rationale: list[str]
    warnings: list[str]


class StrategyOut(ApiModel):
    id: str
    name: str
    version: str
    summary: str
    timeframes: list[str]
    regimes: list[str]
    research_only: bool
    default_entry: list[dict[str, Any]]
