from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.api.deps import AdminDep, DbDep, SettingsDep
from app.core.time import IST, ensure_utc, utcnow
from app.events import store
from app.fundamentals import store as fstore
from app.market_data.factory import ProviderDep
from app.market_data.provider import OPTION_UNDERLYINGS
from app.services.audit import record_audit

router = APIRouter(tags=["events"])


class EventsPayload(BaseModel):
    as_of: datetime
    events: list[dict[str, Any]]
    calendar: list[dict[str, Any]]
    note: str


@router.get("/events", response_model=EventsPayload)
def list_events(provider: ProviderDep, db: DbDep, settings: SettingsDep,
                symbol: Annotated[str | None, Query(max_length=32)] = None,
                from_: Annotated[datetime | None, Query(alias="from")] = None,
                to: Annotated[datetime | None, Query()] = None,
                category: Annotated[str | None, Query(max_length=24)] = None) -> EventsPayload:
    """Imported events known now, plus the deterministic calendar (expiries, holidays)."""
    now = utcnow()
    start = ensure_utc(from_) if from_ else now - timedelta(days=7)
    end = ensure_utc(to) if to else now + timedelta(days=30)
    rows = store.query(db, ts=now, start=start, end=end,
                       symbol=symbol.upper() if symbol else None, category=category)
    cal = store.calendar(settings, provider, OPTION_UNDERLYINGS, now.astimezone(IST).date(),
                         days=max(1, (end - now).days))
    if symbol:
        cal = [c for c in cal if not c["symbols"] or symbol.upper() in c["symbols"]]
    if category:
        cal = [c for c in cal if c["category"] == category]
    return EventsPayload(as_of=now, events=rows, calendar=cal, note=(
        "Events come only from imports of official or licensed sources. Policy, results and "
        "macro dates are never generated. Expiries come from the market-data provider "
        "(SIMULATED in DEMO mode); holidays from configuration."))


@router.get("/events/risk")
def event_risk(_: ProviderDep, db: DbDep, settings: SettingsDep,
               symbol: Annotated[str, Query(max_length=32)],
               underlying: Annotated[str | None, Query(max_length=32)] = None
               ) -> dict[str, Any]:
    sym = symbol.upper()
    now = utcnow()
    prof = fstore.profile(db, sym, now)
    return store.gate_for(db, settings, sym, now, underlying.upper() if underlying else None,
                          prof.sector if prof else None)


@router.post("/admin/events/import")
def import_events(body: store.EventsImport, ctx: AdminDep, db: DbDep,
                  request: Request) -> dict[str, Any]:
    res = store.import_events(db, body)
    record_audit(db, action="events.imported", user_id=ctx.user.id, request=request,
                 details={k: v for k, v in res.items() if k != "ids"})
    db.commit()
    return res
