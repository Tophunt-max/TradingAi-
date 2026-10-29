"""Accounts: DEMO / REAL listing, active-mode switching, the DEMO ledger and demo reset."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.accounts import core as ACC
from app.api.deps import AuthDep, DbDep, SettingsDep
from app.core.errors import AppError, ConflictError
from app.models.accounts import LedgerEntry
from app.schemas.common import Page
from app.services import accounts as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/accounts", tags=["accounts"])

LIVE_CONSENT_VERSION = "draft-0"


class AccountOut(BaseModel):
    id: str | None
    mode: Literal["DEMO", "REAL"]
    status: str
    broker: str | None
    available: bool
    unavailable_reasons: list[str]
    opening_balance: Decimal | None
    cash: Decimal | None
    available_funds: Decimal | None
    equity: Decimal | None
    used_premium: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl_total: Decimal | None
    reset_count: int
    last_reset_at: datetime | None
    created_at: datetime | None
    note: str


class AccountsOut(BaseModel):
    active_mode: Literal["DEMO", "REAL"]
    live_trading_enabled: bool
    accounts: list[AccountOut]


class SetActiveMode(BaseModel):
    mode: Literal["DEMO", "REAL"]


class DemoReset(BaseModel):
    confirm: bool = Field(description="Must be true. Resetting closes demo positions, "
                                      "cancels demo orders and restores the virtual balance.")


class DemoResetOut(BaseModel):
    account: AccountOut
    orders_cancelled: int
    positions_closed: int
    reset_count: int


class LedgerEntryOut(BaseModel):
    id: str
    seq: int
    kind: str
    amount: Decimal
    balance_after: Decimal
    ref_type: str | None
    ref_id: str | None
    note: str | None
    created_at: datetime


def _list(db: DbDep, settings: SettingsDep, user_id) -> AccountsOut:  # type: ignore[no-untyped-def]
    return AccountsOut(
        active_mode=ACC.active_mode(db, user_id),  # type: ignore[arg-type]
        live_trading_enabled=False,
        accounts=[AccountOut(**svc.summary(db, settings, user_id, m)) for m in ("DEMO", "REAL")])


@router.get("", response_model=AccountsOut)
def list_accounts(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> AccountsOut:
    out = _list(db, settings, ctx.user.id)
    db.commit()
    return out


@router.post("/active", response_model=AccountsOut,
             responses={409: {"description": "The REAL account is not available"}})
def set_active(body: SetActiveMode, ctx: AuthDep, db: DbDep, settings: SettingsDep,
               request: Request) -> AccountsOut:
    """Switch which account the app shows and trades in. Never moves orders or positions."""
    try:
        previous = svc.set_active_mode(db, settings, ctx.user.id, body.mode)
    except ConflictError as exc:
        db.rollback()
        record_audit(db, action="account.mode_switch", user_id=ctx.user.id, request=request,
                     outcome="REJECTED", details={"to": body.mode, **(exc.details or {})})
        db.commit()
        raise
    record_audit(db, action="account.mode_switch", user_id=ctx.user.id, request=request,
                 details={"from": previous, "to": body.mode})
    db.commit()
    return _list(db, settings, ctx.user.id)


@router.get("/demo/ledger", response_model=Page[LedgerEntryOut])
def demo_ledger(ctx: AuthDep, db: DbDep,
                limit: Annotated[int, Query(ge=1, le=100)] = 50,
                offset: Annotated[int, Query(ge=0)] = 0) -> Page[LedgerEntryOut]:
    acc = ACC.get_or_create_demo(db, ctx.user.id)
    db.commit()
    total = db.scalar(select(func.count()).select_from(LedgerEntry)
                      .where(LedgerEntry.account_id == acc.id)) or 0
    rows = db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == acc.id)
                      .order_by(LedgerEntry.seq.desc()).limit(limit).offset(offset)).all()
    return Page(items=[LedgerEntryOut(id=str(e.id), seq=e.seq, kind=e.kind, amount=e.amount,
                                      balance_after=e.balance_after, ref_type=e.ref_type,
                                      ref_id=e.ref_id, note=e.note, created_at=e.created_at)
                       for e in rows], total=total, limit=limit, offset=offset)


@router.post("/demo/reset", response_model=DemoResetOut)
def demo_reset(body: DemoReset, ctx: AuthDep, db: DbDep, settings: SettingsDep,
               request: Request) -> DemoResetOut:
    """Close all demo positions at their last known price, cancel all demo orders and
    restore the virtual balance to Rs 10,000. Never touches the REAL account."""
    if not body.confirm:
        raise AppError("Confirm the demo account reset.", code="CONFIRMATION_REQUIRED")
    out = svc.reset_demo(db, settings, ctx.user.id)
    record_audit(db, action="account.demo_reset", user_id=ctx.user.id, request=request,
                 entity_type="account", entity_id=out["account_id"], details=out)
    db.commit()
    return DemoResetOut(account=AccountOut(**svc.summary(db, settings, ctx.user.id, "DEMO")),
                        orders_cancelled=out["orders_cancelled"],
                        positions_closed=out["positions_closed"],
                        reset_count=out["reset_count"])


@router.post("/real/authorize", status_code=409,
             responses={409: {"description": "Live trading is locked in this build"}})
def authorize_live(ctx: AuthDep, db: DbDep, settings: SettingsDep, request: Request) -> None:
    """Granting live-trading authorisation is impossible while live trading is locked.
    Always 409 with the blocking reasons; the attempt is audited."""
    reasons = svc.real_unavailable_reasons(db, settings,
                                           ACC.get_account(db, ctx.user.id, "REAL"))
    record_audit(db, action="account.live_authorization", user_id=ctx.user.id,
                 request=request, outcome="REJECTED",
                 details={"consent_version": LIVE_CONSENT_VERSION, "reasons": reasons})
    db.commit()
    raise AppError("Live trading cannot be authorised in this build.",
                   code="LIVE_TRADING_LOCKED", status_code=409, details={"reasons": reasons})
