from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AdminDep, DbDep, SettingsDep
from app.core.config import MarketDataMode
from app.core.errors import AppError
from app.market_data.factory import get_client
from app.market_data.indstocks.instruments import sync_source
from app.models.audit import AuditLog
from app.schemas.common import Page, PageParams
from app.schemas.user import AuditLogOut
from app.services.audit import record_audit

router = APIRouter(prefix="/admin", tags=["admin"])


class SyncResult(BaseModel):
    source: str
    status: str
    row_count: int
    rejected_rows: int
    message: str | None


@router.post("/market-data/sync-instruments", response_model=list[SyncResult])
def sync_instruments(ctx: AdminDep, db: DbDep, settings: SettingsDep,
                     request: Request) -> list[SyncResult]:
    """Download the INDstocks instrument master (index, equity, fno)."""
    if settings.market_data_mode != MarketDataMode.INDSTOCKS:
        raise AppError("Instrument sync needs MARKET_DATA_MODE=INDSTOCKS.",
                       code="MARKET_DATA_MODE_MISMATCH", status_code=409)
    client = get_client(settings)
    out = []
    for source in ("index", "equity", "fno"):
        log = sync_source(db, client, source)
        out.append(SyncResult(source=source, status=log.status, row_count=log.row_count,
                              rejected_rows=log.rejected_rows, message=log.message))
    record_audit(db, action="admin.instruments_synced", user_id=ctx.user.id, request=request,
                 details={r.source: r.status for r in out})
    db.commit()
    return out


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def list_audit_logs(
    _: AdminDep,
    db: DbDep,
    page: Annotated[PageParams, Depends()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditLog.created_at.desc()).limit(page.limit).offset(page.offset)
    ).all()
    return Page(items=[AuditLogOut.model_validate(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)
