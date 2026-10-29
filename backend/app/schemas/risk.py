from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import ApiModel

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class RiskLimitsOut(ApiModel):
    capital: Decimal
    max_risk_per_trade: Decimal
    daily_loss_limit: Decimal
    daily_profit_lock: Decimal
    max_entries_per_day: int
    max_open_positions: int
    max_lots_per_trade: int
    re_entry_after_stop_loss: bool
    square_off_time_ist: str
    exit_on_loss_limit: bool
    square_off_on_kill_switch: bool
    kill_switch_engaged: bool
    kill_switch_changed_at: datetime | None


class RiskLimitsUpdate(ApiModel):
    capital: Decimal | None = Field(default=None, gt=0, le=Decimal("10000000"))
    max_risk_per_trade: Decimal | None = Field(default=None, gt=0)
    daily_loss_limit: Decimal | None = Field(default=None, gt=0)
    daily_profit_lock: Decimal | None = Field(default=None, gt=0)
    max_entries_per_day: int | None = Field(default=None, ge=1, le=20)
    max_open_positions: int | None = Field(default=None, ge=1, le=5)
    max_lots_per_trade: int | None = Field(default=None, ge=1, le=10)
    re_entry_after_stop_loss: bool | None = None
    square_off_time_ist: str | None = None
    exit_on_loss_limit: bool | None = None
    square_off_on_kill_switch: bool | None = None

    @field_validator("square_off_time_ist")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        if v is not None and (not _HHMM.match(v) or not "09:15" < v <= "15:25"):
            raise ValueError("Square-off must be between 09:16 and 15:25 IST.")
        return v


class RiskUsage(ApiModel):
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    estimated_charges: Decimal
    entries_used: int
    open_positions: int
    planned_risk: Decimal
    daily_loss_used_pct: float
    profit_lock_reached: bool


class RiskStatus(ApiModel):
    mode: str
    # Limits are one policy per user; usage below is for this account mode only.
    account_mode: str = "DEMO"
    engine_active: bool
    engine_note: str
    limits: RiskLimitsOut
    usage: RiskUsage
    new_entries_allowed: bool
    block_reasons: list[str]
    disclaimer: str
    exposure: list[dict[str, Any]] = []


class KillSwitchRequest(ApiModel):
    engaged: bool
    confirm: bool = Field(description="Must be true; protects against accidental taps.")
