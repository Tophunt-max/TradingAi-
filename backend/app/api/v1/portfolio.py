"""Paper portfolio: orders, positions, tradebook, charges and the charges calculator."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.core.errors import AppError, NotFoundError
from app.core.time import IST, utcnow
from app.execution import charges as CH
from app.execution import risk as RISK
from app.execution import service as EX
from app.market_data.factory import ProviderDep, provider_errors
from app.models.execution import ACTIVE_ORDER_STATES, Fill, Order, OrderEvent, Position
from app.schemas.common import Page
from app.services import risk as risk_svc
from app.services.audit import record_audit

router = APIRouter(tags=["portfolio"])
D = Decimal


class PortfolioSummary(BaseModel):
    mode: Literal["PAPER"]
    engine_active: bool
    note: str
    data_source: str | None
    capital: Decimal
    available_funds: Decimal
    used_margin: Decimal
    realized_pnl_today: Decimal
    gross_pnl_today: Decimal
    unrealized_pnl: Decimal
    total_net_pnl: Decimal
    charges_today: Decimal
    trades_today: int
    max_entries_per_day: int
    open_positions: int
    daily_loss_limit: Decimal
    daily_profit_lock: Decimal
    loss_limit_hit: bool
    profit_lock_hit: bool


class OrderOut(BaseModel):
    id: str
    client_order_id: str
    broker_order_id: str | None
    bot_id: str | None
    purpose: str
    instrument: str
    underlying: str
    expiry: date
    strike: Decimal
    option_type: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    lots: int
    lot_size: int
    lot_size_source: str
    order_type: str
    limit_price: Decimal | None
    status: str
    filled_qty: int
    avg_fill_price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None
    reject_reason: str | None
    exit_reason: str | None
    data_source: str
    created_at: datetime


class OrderEventOut(BaseModel):
    ts: datetime
    from_status: str | None
    to_status: str
    message: str


class FillOut(BaseModel):
    broker_fill_id: str
    side: str
    quantity: int
    price: Decimal
    ts: datetime
    charges: dict[str, Any]
    charges_total: Decimal


class OrderDetail(OrderOut):
    events: list[OrderEventOut]
    fills: list[FillOut]
    risk_checks: list[dict[str, Any]]
    charges_estimate: dict[str, Any] | None


class PositionOut(BaseModel):
    id: str
    bot_id: str | None
    instrument: str
    underlying: str
    expiry: date
    strike: Decimal
    option_type: str
    status: str
    quantity: Decimal
    lots: int
    entry_qty: int
    avg_price: Decimal
    avg_exit: Decimal | None
    ltp: Decimal | None
    ltp_at: datetime | None
    unrealized_pnl: Decimal | None
    realized_gross: Decimal
    charges: Decimal
    realized_net: Decimal
    stop_loss: Decimal | None
    target: Decimal | None
    trailing_pct: Decimal | None
    exit_reason: str | None
    data_source: str
    opened_at: datetime
    closed_at: datetime | None


def _order_out(o: Order) -> dict[str, Any]:
    return dict(
        id=str(o.id), client_order_id=o.client_order_id, broker_order_id=o.broker_order_id,
        bot_id=str(o.bot_id) if o.bot_id else None, purpose=o.purpose,
        instrument=o.trading_symbol, underlying=o.underlying, expiry=o.expiry, strike=o.strike,
        option_type=o.option_type, side=o.side, quantity=D(o.quantity),
        lots=o.quantity // o.lot_size, lot_size=o.lot_size, lot_size_source=o.lot_size_source,
        order_type=o.order_type, limit_price=o.limit_price, status=o.status,
        filled_qty=o.filled_qty, avg_fill_price=o.avg_fill_price, stop_price=o.stop_price,
        target_price=o.target_price, reject_reason=o.reject_reason, exit_reason=o.exit_reason,
        data_source=o.data_source, created_at=o.created_at)


def _pos_out(p: Position) -> PositionOut:
    unreal = (p.last_price - p.avg_entry) * p.quantity if p.last_price is not None and \
        p.quantity else None
    return PositionOut(
        id=str(p.id), bot_id=str(p.bot_id) if p.bot_id else None, instrument=p.trading_symbol,
        underlying=p.underlying, expiry=p.expiry, strike=p.strike, option_type=p.option_type,
        status=p.status, quantity=D(p.quantity), lots=p.entry_qty // p.lot_size,
        entry_qty=p.entry_qty, avg_price=p.avg_entry, avg_exit=p.avg_exit, ltp=p.last_price,
        ltp_at=p.last_price_at, unrealized_pnl=unreal, realized_gross=p.realized_gross,
        charges=p.charges_total, realized_net=p.realized_net, stop_loss=p.stop_price,
        target=p.target_price, trailing_pct=p.trailing_pct, exit_reason=p.exit_reason,
        data_source=p.data_source, opened_at=p.opened_at, closed_at=p.closed_at)


@router.get("/portfolio", response_model=PortfolioSummary)
def summary(ctx: AuthDep, db: DbDep) -> PortfolioSummary:
    rs = risk_svc.get_or_create(db, ctx.user.id)
    ds = RISK.daily_state(db, ctx.user.id, RISK.trading_day(utcnow()))
    acct = RISK.account(db, rs, ctx.user.id)
    pos = RISK.open_positions(db, ctx.user.id)
    db.commit()
    src = db.scalar(select(Position.data_source).where(Position.user_id == ctx.user.id)
                    .order_by(Position.opened_at.desc()).limit(1))
    return PortfolioSummary(
        mode="PAPER", engine_active=True,
        note="Simulated paper account. Fills, charges and P&L are simulated and never mixed "
             "with live results.",
        data_source=src, capital=rs.capital, available_funds=acct["available"],
        used_margin=acct["used_margin"] + acct["reserved"],
        realized_pnl_today=ds.realized_net, gross_pnl_today=ds.realized_gross,
        unrealized_pnl=acct["unrealized"],
        total_net_pnl=acct["realized_net_total"] + acct["unrealized"],
        charges_today=ds.charges, trades_today=ds.entries,
        max_entries_per_day=rs.max_entries_per_day, open_positions=len(pos),
        daily_loss_limit=rs.daily_loss_limit, daily_profit_lock=rs.daily_profit_lock,
        loss_limit_hit=ds.loss_limit_hit_at is not None,
        profit_lock_hit=ds.profit_lock_hit_at is not None)


_STATUS_GROUPS = {
    "PENDING": ACTIVE_ORDER_STATES, "EXECUTED": ("FILLED",), "CANCELLED": ("CANCELLED",),
    "REJECTED": ("REJECTED",),
}


@router.get("/orders", response_model=Page[OrderOut])
def orders(
    ctx: AuthDep, db: DbDep,
    status: Annotated[Literal["PENDING", "EXECUTED", "CANCELLED", "REJECTED"] | None,
                      Query()] = None,
    bot_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[OrderOut]:
    stmt = select(Order).where(Order.user_id == ctx.user.id)
    if status:
        stmt = stmt.where(Order.status.in_(_STATUS_GROUPS[status]))
    if bot_id:
        stmt = stmt.where(Order.bot_id == bot_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[OrderOut(**_order_out(o)) for o in rows], total=total, limit=limit,
                offset=offset)


def _owned_order(db: DbDep, user_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    o = db.get(Order, order_id)
    if o is None or o.user_id != user_id:
        raise NotFoundError("Order not found.")
    return o


def _detail(db: DbDep, o: Order) -> OrderDetail:
    ev = db.scalars(select(OrderEvent).where(OrderEvent.order_id == o.id)
                    .order_by(OrderEvent.ts, OrderEvent.id)).all()
    fills = db.scalars(select(Fill).where(Fill.order_id == o.id).order_by(Fill.ts)).all()
    return OrderDetail(
        **_order_out(o),
        events=[OrderEventOut(ts=e.ts, from_status=e.from_status, to_status=e.to_status,
                              message=e.message) for e in ev],
        fills=[FillOut(broker_fill_id=f.broker_fill_id, side=f.side, quantity=f.quantity,
                       price=f.price, ts=f.ts, charges=f.charges,
                       charges_total=f.charges_total) for f in fills],
        risk_checks=o.risk_snapshot.get("checks", []),
        charges_estimate=o.risk_snapshot.get("charges_estimate"))


@router.get("/orders/{order_id}", response_model=OrderDetail)
def order_detail(order_id: uuid.UUID, ctx: AuthDep, db: DbDep) -> OrderDetail:
    return _detail(db, _owned_order(db, ctx.user.id, order_id))


class ManualEntry(BaseModel):
    underlying: Literal["NIFTY", "BANKNIFTY"]
    option_type: Literal["CE", "PE"]
    expiry: date | None = None
    strike: Decimal | None = None
    lots: int = Field(default=1, ge=1, le=10)
    stop_loss_pct: Decimal = Field(default=D("20"), gt=0, lt=100)
    target_pct: Decimal | None = Field(default=D("30"), gt=0, le=1000)
    trailing_pct: Decimal | None = Field(default=None, gt=0, lt=100)
    idempotency_key: str = Field(min_length=8, max_length=48, pattern=r"^[A-Za-z0-9\-]+$")


@router.post("/orders/paper", response_model=OrderDetail, status_code=201)
def manual_entry(body: ManualEntry, ctx: AuthDep, db: DbDep, settings: SettingsDep,
                 provider: ProviderDep, request: Request) -> OrderDetail:
    """Manual paper BUY of one option contract. Goes through the same risk manager."""
    now = utcnow()
    cache = EX.ChainCache(provider, settings)
    try:
        with provider_errors():
            o = EX.place_entry(
                db, settings, cache, user_id=ctx.user.id, now=now,
                option_type=body.option_type, underlying=body.underlying,
                stop={"type": "PERCENT", "value": str(body.stop_loss_pct)},
                target={"type": "PERCENT", "value": str(body.target_pct)}
                if body.target_pct else None,
                trailing={"type": "PERCENT", "value": str(body.trailing_pct)}
                if body.trailing_pct else None,
                client_order_id=f"MAN-{ctx.user.id.hex[:8]}-{body.idempotency_key}",
                lots=body.lots, strike=body.strike, expiry=body.expiry)
            EX.fill_now(db, settings, cache, o, now)
    except EX.ExecutionError as exc:
        db.rollback()
        raise AppError(exc.message, code=exc.code, status_code=422) from exc
    record_audit(db, action="order.manual_entry", user_id=ctx.user.id, request=request,
                 entity_type="order", entity_id=str(o.id),
                 details={"status": o.status, "symbol": o.trading_symbol})
    db.commit()
    return _detail(db, o)


@router.post("/orders/{order_id}/cancel", response_model=OrderDetail)
def cancel_order(order_id: uuid.UUID, ctx: AuthDep, db: DbDep, settings: SettingsDep,
                 provider: ProviderDep, request: Request) -> OrderDetail:
    o = _owned_order(db, ctx.user.id, order_id)
    if o.status not in ("ACKNOWLEDGED", "PARTIALLY_FILLED", "SUBMITTED"):
        raise AppError(f"An order that is {o.status} cannot be cancelled.",
                       code="ORDER_NOT_CANCELLABLE", status_code=409)
    EX.cancel(db, settings, EX.ChainCache(provider, settings), o, "Cancelled by user.")
    record_audit(db, action="order.cancelled", user_id=ctx.user.id, request=request,
                 entity_type="order", entity_id=str(o.id))
    db.commit()
    return _detail(db, o)


@router.get("/positions", response_model=Page[PositionOut])
def positions(
    ctx: AuthDep, db: DbDep,
    state: Annotated[Literal["OPEN", "CLOSED"], Query()] = "OPEN",
    bot_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[PositionOut]:
    stmt = select(Position).where(Position.user_id == ctx.user.id)
    stmt = stmt.where(Position.status == "CLOSED") if state == "CLOSED" else \
        stmt.where(Position.status != "CLOSED")
    if bot_id:
        stmt = stmt.where(Position.bot_id == bot_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order_col = Position.closed_at.desc() if state == "CLOSED" else Position.opened_at.desc()
    rows = db.scalars(stmt.order_by(order_col).limit(limit).offset(offset)).all()
    return Page(items=[_pos_out(p) for p in rows], total=total, limit=limit, offset=offset)


@router.post("/positions/{position_id}/exit", response_model=PositionOut)
def exit_position(position_id: uuid.UUID, ctx: AuthDep, db: DbDep, settings: SettingsDep,
                  provider: ProviderDep, request: Request) -> PositionOut:
    p = db.get(Position, position_id)
    if p is None or p.user_id != ctx.user.id:
        raise NotFoundError("Position not found.")
    if p.status == "CLOSED":
        raise AppError("Position is already closed.", code="POSITION_CLOSED", status_code=409)
    now = utcnow()
    cache = EX.ChainCache(provider, settings)
    o = EX.place_exit(db, settings, cache, p, "MANUAL", now)
    if o is not None:
        EX.fill_now(db, settings, cache, o, now)
    record_audit(db, action="position.manual_exit", user_id=ctx.user.id, request=request,
                 entity_type="position", entity_id=str(p.id))
    db.commit()
    db.refresh(p)
    return _pos_out(p)


@router.get("/trades.csv")
def trades_csv(ctx: AuthDep, db: DbDep) -> StreamingResponse:
    rows = db.scalars(select(Position).where(Position.user_id == ctx.user.id,
                                             Position.status == "CLOSED")
                      .order_by(Position.closed_at)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "data_source", "instrument", "qty", "entry_time_ist", "avg_entry",
                "exit_time_ist", "avg_exit", "gross_pnl", "charges", "net_pnl", "exit_reason",
                "bot_id"])
    for p in rows:
        w.writerow(["PAPER", p.data_source, p.trading_symbol, p.entry_qty,
                    p.opened_at.astimezone(IST).isoformat(timespec="seconds"), p.avg_entry,
                    p.closed_at.astimezone(IST).isoformat(timespec="seconds") if p.closed_at
                    else "", p.avg_exit, p.realized_gross, p.charges_total, p.realized_net,
                    p.exit_reason, p.bot_id or ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition":
                                      "attachment; filename=quantai-paper-trades.csv"})


@router.get("/portfolio/charges")
def charges_breakdown(ctx: AuthDep, db: DbDep,
                      day: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    q = select(Fill).where(Fill.user_id == ctx.user.id)
    if day:
        start = datetime.combine(day, time(0), IST)
        q = q.where(Fill.ts >= start, Fill.ts < start + timedelta(days=1))
    total: dict[str, Decimal] = {}
    n = 0
    for f in db.scalars(q).all():
        n += 1
        for k, v in f.charges.items():
            if k != "schedule_id":
                total[k] = total.get(k, D("0")) + D(v)
    return {"fills": n, "breakdown": {k: str(v) for k, v in total.items()},
            "note": "Estimated from versioned schedules; reconcile with broker contract notes."}


class ChargesEstimate(BaseModel):
    buy_price: Decimal = Field(gt=0)
    sell_price: Decimal = Field(gt=0)
    quantity: int = Field(ge=1, le=100000)
    trade_date: date | None = None


@router.post("/charges/estimate")
def charges_estimate(body: ChargesEstimate, _: AuthDep, settings: SettingsDep) -> dict[str, Any]:
    day = body.trade_date or utcnow().astimezone(IST).date()
    try:
        out = CH.round_trip(buy=body.buy_price, sell=body.sell_price, qty=body.quantity,
                            day=day, brokerage=settings.paper_brokerage_per_order)
    except ValueError as exc:
        raise AppError(str(exc), code="NO_CHARGE_SCHEDULE", status_code=422) from exc
    s = CH.schedule_for(day)
    return {**out, "brokerage_per_order": str(settings.paper_brokerage_per_order),
            "schedule_detail": {"id": s.id, "effective_from": s.effective_from.isoformat(),
                                "stt_sell_pct": str(s.stt_sell_pct),
                                "exchange_txn_pct": str(s.exchange_txn_pct),
                                "sebi_pct": str(s.sebi_pct), "stamp_buy_pct": str(s.stamp_buy_pct),
                                "gst_pct": str(s.gst_pct), "source": s.source}}
