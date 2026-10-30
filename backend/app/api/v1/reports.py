"""Reports: daily report, P&L analytics and CSV exports for ONE account (the explicit
`account_mode`, else the active mode). See docs/reports.md."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select

from app.accounts import core as ACC
from app.api.deps import AuthDep, DbDep
from app.core.errors import AppError
from app.core.time import IST, utcnow
from app.models.reports import DailyReport
from app.reports import exports as X
from app.reports.daily import MAX_RANGE_DAYS, daily_report, pnl_analytics

router = APIRouter(prefix="/reports", tags=["reports"])

ModeQuery = Annotated[Literal["DEMO", "REAL"] | None,
                      Query(description="Account to report on; defaults to the active mode.")]
DayQuery = Annotated[date | None, Query(description="IST trading day; defaults to today.")]
CSV_MAX_DAYS = 92


def _today() -> date:
    return utcnow().astimezone(IST).date()


def _range(start: date | None, end: date | None, default_days: int, max_days: int
           ) -> tuple[date, date]:
    end = end or _today()
    start = start or end - timedelta(days=default_days - 1)
    if start > end:
        raise AppError("`from` must not be after `to`.", code="INVALID_RANGE", status_code=422)
    if (end - start).days + 1 > max_days:
        raise AppError(f"The range is limited to {max_days} days.", code="RANGE_TOO_LARGE",
                       status_code=422)
    return start, end


def _csv(body: str, mode: str, name: str) -> Response:
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=quantai-{mode.lower()}-{name}.csv"})


@router.get("/daily")
def daily(ctx: AuthDep, db: DbDep, day: DayQuery = None,
          account_mode: ModeQuery = None) -> dict[str, Any]:
    mode, acc = ACC.resolve(db, ctx.user.id, account_mode)
    db.commit()
    day = day or _today()
    out = daily_report(db, acc, mode, day)
    stored = db.scalar(select(DailyReport).where(DailyReport.account_id == acc.id,
                                                 DailyReport.day == day)) if acc else None
    out["stored_at"] = stored.generated_at.isoformat() if stored else None
    return out


@router.get("/pnl")
def pnl(ctx: AuthDep, db: DbDep,
        from_: Annotated[date | None, Query(alias="from")] = None,
        to: Annotated[date | None, Query()] = None,
        account_mode: ModeQuery = None) -> dict[str, Any]:
    start, end = _range(from_, to, 30, MAX_RANGE_DAYS)
    mode, acc = ACC.resolve(db, ctx.user.id, account_mode)
    db.commit()
    return pnl_analytics(db, acc, mode, start, end)


@router.get("/orders.csv")
def orders_csv(ctx: AuthDep, db: DbDep, account_mode: ModeQuery = None) -> Response:
    mode, acc = ACC.resolve(db, ctx.user.id, account_mode)
    db.commit()
    return _csv(X.orders_csv(db, acc), mode, "orders")


@router.get("/fills.csv")
def fills_csv(ctx: AuthDep, db: DbDep, account_mode: ModeQuery = None) -> Response:
    mode, acc = ACC.resolve(db, ctx.user.id, account_mode)
    db.commit()
    return _csv(X.fills_csv(db, acc), mode, "fills")


@router.get("/ledger.csv")
def ledger_csv(ctx: AuthDep, db: DbDep, account_mode: ModeQuery = None) -> Response:
    mode, acc = ACC.resolve(db, ctx.user.id, account_mode)
    db.commit()
    if mode != "DEMO" or acc is None:
        raise AppError("Only the DEMO account has a cash ledger. Real balances come from the "
                       "broker.", code="LEDGER_DEMO_ONLY", status_code=409)
    return _csv(X.ledger_csv(db, acc), mode, "ledger")


@router.get("/daily-report.csv")
def daily_report_csv(ctx: AuthDep, db: DbDep,
                     from_: Annotated[date | None, Query(alias="from")] = None,
                     to: Annotated[date | None, Query()] = None,
                     account_mode: ModeQuery = None) -> Response:
    start, end = _range(from_, to, 30, CSV_MAX_DAYS)
    mode, acc = ACC.resolve(db, ctx.user.id, account_mode)
    db.commit()
    return _csv(X.daily_csv(db, acc, mode, start, end), mode, "daily-report")


@router.get("/trades.csv")
def trades_csv(ctx: AuthDep, db: DbDep, account_mode: ModeQuery = None) -> Response:
    """Same rows and columns as GET /trades.csv (kept for compatibility)."""
    from app.api.v1.portfolio import trades_csv as legacy

    return legacy(ctx, db, account_mode)  # type: ignore[return-value]
