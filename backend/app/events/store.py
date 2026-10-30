"""Market-event import (with novelty detection), queries and the calendar.

Novelty: the fingerprint is sha256(category | sorted symbols | event_time | normalised
title). A row whose fingerprint and content hash both match an existing row is a
DUPLICATE; same fingerprint with different content (summary, values, importance,
direction) is an UPDATE and links `supersedes_id` to the latest earlier version.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import ensure_utc, utcnow
from app.events import risk as RISK
from app.models.intelligence import MarketEvent

logger = logging.getLogger("app.events")

Category = Literal["EARNINGS", "CORPORATE_ANNOUNCEMENT", "CORPORATE_ACTION", "REGULATORY",
                   "MONETARY_POLICY", "INFLATION", "EMPLOYMENT", "GDP", "EXPIRY", "HOLIDAY",
                   "OTHER"]
CATEGORIES = Category.__args__  # type: ignore[attr-defined]


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    scope: Literal["INSTRUMENT", "SECTOR", "INDEX", "MARKET"]
    symbols: list[str] = Field(default_factory=list, max_length=200)
    title: str = Field(min_length=3, max_length=300)
    summary: str | None = Field(default=None, max_length=5000)
    source: str = Field(min_length=2, max_length=80)
    source_reliability: Literal["OFFICIAL", "LICENSED_VENDOR", "OTHER"]
    url: str | None = Field(default=None, max_length=500)
    event_time: datetime
    published_at: datetime
    available_at: datetime | None = None
    expected_value: Decimal | None = None
    actual_value: Decimal | None = None
    unit: str | None = Field(default=None, max_length=16)
    importance: Literal["LOW", "MEDIUM", "HIGH"]
    direction_hint: Literal["UP", "DOWN", "MIXED"] | None = None
    uncertainty_note: str | None = Field(default=None, max_length=300)

    @field_validator("symbols")
    @classmethod
    def _syms(cls, v: list[str]) -> list[str]:
        out = sorted({s.strip().upper() for s in v if s.strip()})
        if any(len(s) > 80 for s in out):
            raise ValueError("Symbol or sector names must be at most 80 characters.")
        return out

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("https://", "http://")):
            raise ValueError("url must be http(s).")
        return v

    @model_validator(mode="after")
    def _check(self) -> EventIn:
        now = utcnow()
        for n in ("event_time", "published_at", "available_at"):
            v = getattr(self, n)
            if v is not None and v.tzinfo is None:
                raise ValueError(f"{n} must include a timezone.")
        if ensure_utc(self.published_at) > now:
            raise ValueError("published_at is in the future.")
        if self.available_at is not None:
            if ensure_utc(self.available_at) > now:
                raise ValueError("available_at is in the future; the system cannot know it yet.")
            if self.available_at < self.published_at:
                raise ValueError("available_at cannot be earlier than published_at.")
        if self.scope != "MARKET" and not self.symbols:
            raise ValueError("Non-MARKET events must list the affected symbols/sectors/indices.")
        if self.direction_hint is not None and not self.uncertainty_note:
            raise ValueError("A direction_hint needs an uncertainty_note from the source.")
        return self


class EventsImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[EventIn] = Field(max_length=5000)


def normalise_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", t.lower())).strip()


def fingerprint(category: str, symbols: list[str], event_time: datetime, title: str) -> str:
    key = "|".join([category, ",".join(sorted(s.upper() for s in symbols)),
                    ensure_utc(event_time).isoformat(), normalise_title(title)])
    return hashlib.sha256(key.encode()).hexdigest()


def content_hash(e: EventIn) -> str:
    key = "|".join(str(x) for x in (e.summary or "", e.expected_value, e.actual_value, e.unit,
                                    e.importance, e.direction_hint, e.uncertainty_note))
    return hashlib.sha256(key.encode()).hexdigest()


def import_events(db: Session, body: EventsImport, now: datetime | None = None
                  ) -> dict[str, Any]:
    now = now or utcnow()
    res = {"NEW": 0, "UPDATE": 0, "DUPLICATE": 0, "ids": []}
    for e in body.events:
        fp, ch = fingerprint(e.category, e.symbols, e.event_time, e.title), content_hash(e)
        prior = db.scalars(select(MarketEvent).where(MarketEvent.fingerprint == fp)
                           .order_by(MarketEvent.available_at, MarketEvent.created_at)).all()
        originals = [p for p in prior if p.novelty != "DUPLICATE"]
        if any(p.content_hash == ch for p in originals):
            novelty = "DUPLICATE"
            sup = next(p for p in originals if p.content_hash == ch).id
        elif originals:
            novelty, sup = "UPDATE", originals[-1].id
        else:
            novelty, sup = "NEW", None
        row = MarketEvent(
            category=e.category, scope=e.scope, symbols=e.symbols, title=e.title,
            summary=e.summary, source=e.source, source_reliability=e.source_reliability,
            url=e.url, event_time=ensure_utc(e.event_time),
            published_at=ensure_utc(e.published_at),
            available_at=ensure_utc(e.available_at) if e.available_at else now,
            expected_value=e.expected_value, actual_value=e.actual_value, unit=e.unit,
            novelty=novelty, supersedes_id=sup, importance=e.importance,
            direction_hint=e.direction_hint, uncertainty_note=e.uncertainty_note,
            fingerprint=fp, content_hash=ch)
        db.add(row)
        db.flush()
        res[novelty] += 1
        res["ids"].append(str(row.id))
    return res


def as_dict(e: MarketEvent) -> dict[str, Any]:
    return {"id": e.id, "category": e.category, "scope": e.scope, "symbols": e.symbols,
            "title": e.title, "summary": e.summary, "source": e.source,
            "source_reliability": e.source_reliability, "url": e.url,
            "event_time": e.event_time, "published_at": e.published_at,
            "available_at": e.available_at, "expected_value": e.expected_value,
            "actual_value": e.actual_value, "unit": e.unit, "novelty": e.novelty,
            "supersedes_id": e.supersedes_id, "importance": e.importance,
            "direction_hint": e.direction_hint, "uncertainty_note": e.uncertainty_note}


def query(db: Session, *, ts: datetime, start: datetime | None = None,
          end: datetime | None = None, symbol: str | None = None,
          category: str | None = None, include_duplicates: bool = False
          ) -> list[dict[str, Any]]:
    """Events known at ts (available_at <= ts), optionally filtered by event_time."""
    stmt = select(MarketEvent).where(MarketEvent.available_at <= ts)
    if start:
        stmt = stmt.where(MarketEvent.event_time >= start)
    if end:
        stmt = stmt.where(MarketEvent.event_time <= end)
    if category:
        stmt = stmt.where(MarketEvent.category == category)
    rows = [as_dict(e) for e in db.scalars(stmt.order_by(MarketEvent.event_time)).all()]
    if not include_duplicates:
        rows = RISK.visible(rows, ts)
    if symbol:
        rows = [e for e in rows if RISK.relevant(e, symbol, None, None)]
    return rows


def gate_for(db: Session, settings: Settings, symbol: str, ts: datetime,
             underlying: str | None = None, sector: str | None = None) -> dict[str, Any]:
    before = timedelta(minutes=settings.event_risk_before_minutes)
    after = timedelta(minutes=settings.event_risk_after_minutes)
    ts = ensure_utc(ts)
    rows = [as_dict(e) for e in db.scalars(select(MarketEvent).where(
        MarketEvent.available_at <= ts, MarketEvent.event_time >= ts - after,
        MarketEvent.event_time <= ts + before)).all()]
    return RISK.event_risk(rows, symbol, underlying, ts, before, after, sector)


def calendar(settings: Settings, provider: Any, underlyings: tuple[str, ...], today: date,
             days: int = 30) -> list[dict[str, Any]]:
    """Deterministic calendar entries the system actually has: expiries and holidays.

    Expiries come from the provider (instrument master); DEMO expiries are labelled
    SIMULATED. Holidays come from settings.market_holidays. Policy or results dates
    are never generated: they only appear if imported as events.
    """
    out: list[dict[str, Any]] = []
    horizon = today + timedelta(days=days)
    for u in underlyings:
        try:
            r = provider.expiries(u)
        except Exception:  # noqa: BLE001 - an unavailable provider just omits expiries
            logger.warning("calendar_expiries_unavailable", extra={"underlying": u})
            continue
        label = "SIMULATED" if r.source == "DEMO" else r.source
        for d in r.data:
            d = d if isinstance(d, date) else date.fromisoformat(str(d))
            if today <= d <= horizon:
                out.append({"date": d.isoformat(), "category": "EXPIRY", "symbols": [u],
                            "title": f"{u} options expiry", "label": label,
                            "source": f"{r.provider} expiries"})
    for h in settings.market_holidays:
        try:
            d = date.fromisoformat(h)
        except ValueError:
            continue
        if today <= d <= horizon:
            out.append({"date": d.isoformat(), "category": "HOLIDAY", "symbols": [],
                        "title": "Market holiday", "label": "CONFIGURED",
                        "source": "settings.market_holidays"})
    return sorted(out, key=lambda x: (x["date"], x["category"]))
