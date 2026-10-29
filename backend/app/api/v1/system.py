from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import SettingsDep
from app.core.config import MarketDataMode

router = APIRouter(prefix="/system", tags=["system"])


class SystemStatus(BaseModel):
    environment: str
    api_version: str
    market_data_mode: MarketDataMode
    is_demo_data: bool
    paper_trading_enabled: bool
    live_trading_enabled: bool
    live_trading_blockers: list[str]
    disclaimer: str


LIVE_BLOCKERS = [
    "No verified broker adapter is integrated.",
    "No authorised real-time market-data provider is integrated.",
    "Global risk manager and order state machine are not implemented yet.",
    "Explicit user authorisation for live trading has not been granted.",
]


@router.get("/status", response_model=SystemStatus)
def status(settings: SettingsDep) -> SystemStatus:
    return SystemStatus(
        environment=settings.environment.value,
        api_version="v1",
        market_data_mode=settings.market_data_mode,
        is_demo_data=settings.market_data_mode == MarketDataMode.DEMO,
        paper_trading_enabled=settings.paper_trading_enabled,
        live_trading_enabled=False,
        live_trading_blockers=LIVE_BLOCKERS,
        disclaimer=(
            "QuantAI F&O provides analysis tools only. Trading in derivatives involves "
            "substantial risk of loss. No profits or prediction accuracy are guaranteed."
        ),
    )
