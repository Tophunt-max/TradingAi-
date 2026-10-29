"""Portfolio, orders and positions.

There is no execution engine yet, so these endpoints return the user's real
(empty) paper ledger. They exist now so the app is wired to the final API
contract; Phase 6 fills them from the order state machine and fills.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import AuthDep, DbDep
from app.schemas.common import Page
from app.services import risk as risk_svc

router = APIRouter(tags=["portfolio"])

ENGINE_NOTE = "Paper execution starts in Phase 6. No orders or positions exist yet."


class PortfolioSummary(BaseModel):
    mode: Literal["PAPER"]
    engine_active: bool
    note: str
    capital: Decimal
    available_funds: Decimal
    used_margin: Decimal
    realized_pnl_today: Decimal
    unrealized_pnl: Decimal
    total_net_pnl: Decimal
    charges_today: Decimal
    trades_today: int
    max_entries_per_day: int
    open_positions: int
    daily_loss_limit: Decimal
    daily_profit_lock: Decimal


class OrderOut(BaseModel):
    id: str
    broker_order_id: str | None
    instrument: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    order_type: str
    status: str
    avg_fill_price: Decimal | None
    created_at: datetime


class PositionOut(BaseModel):
    id: str
    instrument: str
    quantity: Decimal
    avg_price: Decimal
    ltp: Decimal | None
    unrealized_pnl: Decimal | None
    stop_loss: Decimal | None
    target: Decimal | None
    opened_at: datetime


@router.get("/portfolio", response_model=PortfolioSummary)
def summary(ctx: AuthDep, db: DbDep) -> PortfolioSummary:
    rs = risk_svc.get_or_create(db, ctx.user.id)
    db.commit()
    z = Decimal("0")
    return PortfolioSummary(
        mode="PAPER", engine_active=False, note=ENGINE_NOTE, capital=rs.capital,
        available_funds=rs.capital, used_margin=z, realized_pnl_today=z, unrealized_pnl=z,
        total_net_pnl=z, charges_today=z, trades_today=0,
        max_entries_per_day=rs.max_entries_per_day, open_positions=0,
        daily_loss_limit=rs.daily_loss_limit, daily_profit_lock=rs.daily_profit_lock,
    )


@router.get("/orders", response_model=Page[OrderOut])
def orders(
    _: AuthDep,
    status: Annotated[Literal["PENDING", "EXECUTED", "CANCELLED", "REJECTED"] | None,
                      Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[OrderOut]:
    return Page(items=[], total=0, limit=limit, offset=offset)


@router.get("/positions", response_model=Page[PositionOut])
def positions(
    _: AuthDep,
    state: Annotated[Literal["OPEN", "CLOSED"], Query()] = "OPEN",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[PositionOut]:
    return Page(items=[], total=0, limit=limit, offset=offset)
