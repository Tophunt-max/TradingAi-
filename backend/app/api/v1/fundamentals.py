from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel

from app.api.deps import AdminDep, DbDep, SettingsDep
from app.core.errors import AppError
from app.core.time import ensure_utc, utcnow
from app.fundamentals import service, store
from app.market_data.factory import ProviderDep
from app.services.audit import record_audit

router = APIRouter(tags=["fundamentals"])


class FundamentalsPayload(BaseModel):
    symbol: str
    as_of: datetime
    status: Literal["OK", "NO_DATA", "INDEX"]
    message: str | None = None
    profile: dict[str, Any] | None
    periods: list[dict[str, Any]]
    ratios: dict[str, Any] | None
    returns: dict[str, Any] | None
    ttm_basis: str | None = None
    valuation: dict[str, Any] | None
    price: dict[str, Any] | None
    peers: dict[str, Any] | None
    history: dict[str, Any] | None
    constituents: dict[str, Any] | None = None
    corporate_actions: list[dict[str, Any]]
    flags: list[str]
    explanation: dict[str, list[str]]


@router.get("/fundamentals/{symbol}", response_model=FundamentalsPayload)
def get_fundamentals(provider: ProviderDep, db: DbDep, settings: SettingsDep,
                     symbol: Annotated[str, Path(max_length=32)],
                     as_of: Annotated[datetime | None, Query()] = None) -> FundamentalsPayload:
    """Point-in-time fundamentals. `as_of` defaults to now; future times are rejected."""
    if as_of is not None and ensure_utc(as_of) > utcnow():
        raise AppError("as_of cannot be in the future.", code="INVALID_AS_OF", status_code=422)
    out = service.build(db, provider, symbol.upper(), as_of, settings.fundamentals_stale_days)
    return FundamentalsPayload(**out)


@router.post("/admin/fundamentals/import")
def import_fundamentals(body: store.FundamentalsImport, ctx: AdminDep, db: DbDep,
                        request: Request) -> dict[str, Any]:
    """Import statements, profiles and corporate actions from a licensed source."""
    res = store.import_all(db, body)
    record_audit(db, action="fundamentals.imported", user_id=ctx.user.id, request=request,
                 details={**res, "sources": sorted({r.source for r in body.reports})})
    db.commit()
    return res
