from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import AdminDep, DbDep
from app.models.audit import AuditLog
from app.schemas.common import Page, PageParams
from app.schemas.user import AuditLogOut

router = APIRouter(prefix="/admin", tags=["admin"])


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
