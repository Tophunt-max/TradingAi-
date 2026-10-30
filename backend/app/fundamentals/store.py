"""Fundamentals storage: validated import and point-in-time reads.

Point-in-time rule: at decision time `ts`, a report is visible only if
`published_at <= ts` and `available_at <= ts`. For each (period, series) the
highest visible revision wins. Series: ACTUAL (REPORTED and RESTATED share it, a
restatement is a later revision), ADJUSTED, ESTIMATE. Rows are never updated.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utcnow
from app.fundamentals import ratios as RT
from app.models.intelligence import (
    FUNDAMENTAL_LINE_ITEMS,
    CompanyProfile,
    CorporateAction,
    FundamentalReport,
)

Basis = Literal["REPORTED", "ESTIMATED", "ADJUSTED", "RESTATED"]


def series(basis: str) -> str:
    return {"ESTIMATED": "ESTIMATE", "ADJUSTED": "ADJUSTED"}.get(basis, "ACTUAL")


def _sym(v: str) -> str:
    v = v.strip().upper()
    if not v or len(v) > 32 or not all(c.isalnum() or c in "-&_." for c in v):
        raise ValueError("Symbol must be 1–32 characters: letters, digits, - & _ .")
    return v


class _Timed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=2, max_length=80)
    source_ref: str | None = Field(default=None, max_length=200)
    available_at: datetime | None = None  # defaults to the import time

    @model_validator(mode="after")
    def _times(self) -> Any:
        now = utcnow()
        for name in ("published_at", "announced_at", "available_at"):
            v = getattr(self, name, None)
            if v is None:
                continue
            if v.tzinfo is None:
                raise ValueError(f"{name} must include a timezone (e.g. +05:30 or Z).")
            if ensure_utc(v) > now:
                raise ValueError(f"{name} is in the future; the system cannot know it yet.")
        pub = getattr(self, "published_at", None) or getattr(self, "announced_at", None)
        if self.available_at is not None and pub is not None and self.available_at < pub:
            raise ValueError("available_at cannot be earlier than the publication time.")
        return self


class ReportIn(_Timed):
    symbol: str
    period_end: date
    period_type: Literal["Q", "FY"]
    value_basis: Basis = "REPORTED"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    published_at: datetime
    revenue: Decimal | None = None
    gross_profit: Decimal | None = None
    operating_income: Decimal | None = None
    net_income: Decimal | None = None
    eps: Decimal | None = None
    operating_cash_flow: Decimal | None = None
    capex: Decimal | None = Field(default=None, ge=0, description="Positive outflow.")
    depreciation_amortization: Decimal | None = Field(default=None, ge=0)
    total_debt: Decimal | None = Field(default=None, ge=0)
    interest_expense: Decimal | None = Field(default=None, ge=0)
    equity: Decimal | None = None
    capital_employed: Decimal | None = None
    total_assets: Decimal | None = Field(default=None, ge=0)
    current_assets: Decimal | None = Field(default=None, ge=0)
    current_liabilities: Decimal | None = None
    shares_outstanding: Decimal | None = Field(default=None, gt=0)
    dividends_per_share: Decimal | None = Field(default=None, ge=0)
    cash: Decimal | None = Field(default=None, ge=0)

    @field_validator("symbol")
    @classmethod
    def check_symbol(cls, v: str) -> str:
        return _sym(v)


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None = Field(default=None, max_length=120)
    sector: str | None = Field(default=None, max_length=80)
    industry: str | None = Field(default=None, max_length=80)
    peer_group: str | None = Field(default=None, max_length=80)
    is_index: bool = False
    constituents: list[dict[str, Any]] | None = None
    source: str = Field(min_length=2, max_length=80)
    # When this classification became known to the system (defaults to import time).
    available_at: datetime | None = None

    @field_validator("available_at")
    @classmethod
    def _avail(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or ensure_utc(v) > utcnow()):
            raise ValueError("available_at must be timezone-aware and not in the future.")
        return v

    @field_validator("symbol")
    @classmethod
    def check_symbol(cls, v: str) -> str:
        return _sym(v)

    @field_validator("constituents")
    @classmethod
    def _weights(cls, v: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if v is None:
            return v
        out = []
        for c in v:
            w = float(c.get("weight", -1))
            if not 0 < w <= 1:
                raise ValueError("Constituent weight must be in (0, 1].")
            out.append({"symbol": _sym(str(c.get("symbol", ""))), "weight": w})
        return out


class ActionIn(_Timed):
    symbol: str
    action_type: Literal["SPLIT", "BONUS", "DIVIDEND", "RIGHTS"]
    ex_date: date
    record_date: date | None = None
    ratio_from: Decimal | None = Field(default=None, gt=0)
    ratio_to: Decimal | None = Field(default=None, gt=0)
    amount: Decimal | None = Field(default=None, ge=0)
    announced_at: datetime

    @field_validator("symbol")
    @classmethod
    def check_symbol(cls, v: str) -> str:
        return _sym(v)


class FundamentalsImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reports: list[ReportIn] = Field(default_factory=list, max_length=5000)
    profiles: list[ProfileIn] = Field(default_factory=list, max_length=2000)
    corporate_actions: list[ActionIn] = Field(default_factory=list, max_length=5000)


Q4 = Decimal("0.0001")


def _items(obj: Any) -> dict[str, Decimal | None]:
    """Line items at the stored precision (4 decimal places)."""
    out = {}
    for k in FUNDAMENTAL_LINE_ITEMS:
        v = getattr(obj, k)
        out[k] = None if v is None else Decimal(v).quantize(Q4)
    return out


def _same(a: dict[str, Decimal | None], b: dict[str, Decimal | None]) -> bool:
    return all((a[k] is None and b[k] is None) or (a[k] is not None and b[k] is not None
               and Decimal(a[k]) == Decimal(b[k])) for k in FUNDAMENTAL_LINE_ITEMS)


def import_all(db: Session, body: FundamentalsImport, now: datetime | None = None
               ) -> dict[str, Any]:
    now = now or utcnow()
    res = {"reports_added": 0, "reports_unchanged": 0, "restatements": 0,
           "profiles_upserted": 0, "actions_added": 0, "actions_unchanged": 0}
    for r in body.reports:
        avail = ensure_utc(r.available_at) if r.available_at else now
        prior = db.scalars(select(FundamentalReport).where(
            FundamentalReport.symbol == r.symbol, FundamentalReport.period_end == r.period_end,
            FundamentalReport.period_type == r.period_type)).all()
        same_series = [p for p in prior if series(p.value_basis) == series(r.value_basis)]
        latest = max(same_series, key=lambda p: p.revision, default=None)
        items = _items(r)
        if any(_same(_items(p), items) for p in same_series):
            res["reports_unchanged"] += 1
            continue
        flags = RT.quality_flags({"items": items})
        if latest is not None:
            flags.append(f"REVISION_OF_REVISION_{latest.revision}")
            res["restatements"] += 1
        revision = 0 if not prior else max(p.revision for p in prior) + 1
        db.add(FundamentalReport(
            symbol=r.symbol, period_end=r.period_end, period_type=r.period_type,
            value_basis=r.value_basis, currency=r.currency.upper(), **items, source=r.source,
            source_ref=r.source_ref, published_at=ensure_utc(r.published_at),
            available_at=avail, revision=revision, quality_flags=flags))
        db.flush()
        res["reports_added"] += 1
    for p in body.profiles:
        row = db.scalar(select(CompanyProfile).where(CompanyProfile.symbol == p.symbol))
        data = p.model_dump()
        data["available_at"] = ensure_utc(p.available_at) if p.available_at else now
        if row is None:
            db.add(CompanyProfile(**data))
        else:
            for k, v in data.items():
                setattr(row, k, v)
        res["profiles_upserted"] += 1
    for a in body.corporate_actions:
        exists = db.scalar(select(func.count()).select_from(CorporateAction).where(
            CorporateAction.symbol == a.symbol, CorporateAction.action_type == a.action_type,
            CorporateAction.ex_date == a.ex_date, CorporateAction.source == a.source))
        if exists:
            res["actions_unchanged"] += 1
            continue
        db.add(CorporateAction(
            symbol=a.symbol, action_type=a.action_type, ex_date=a.ex_date,
            record_date=a.record_date, ratio_from=a.ratio_from, ratio_to=a.ratio_to,
            amount=a.amount, announced_at=ensure_utc(a.announced_at),
            available_at=ensure_utc(a.available_at) if a.available_at else now,
            source=a.source, source_ref=a.source_ref))
        db.flush()
        res["actions_added"] += 1
    return res


def _period(r: FundamentalReport) -> dict[str, Any]:
    return {"period_end": r.period_end, "period_type": r.period_type,
            "value_basis": r.value_basis, "revision": r.revision, "currency": r.currency,
            "items": _items(r), "source": r.source, "source_ref": r.source_ref,
            "published_at": r.published_at, "available_at": r.available_at,
            "quality_flags": list(r.quality_flags or [])}


def as_of(db: Session, symbol: str, ts: datetime) -> list[dict[str, Any]]:
    """Latest visible revision of every (period, series) at `ts`, oldest first."""
    ts = ensure_utc(ts)
    rows = db.scalars(select(FundamentalReport).where(
        FundamentalReport.symbol == symbol, FundamentalReport.available_at <= ts,
        FundamentalReport.published_at <= ts)).all()
    best: dict[tuple, FundamentalReport] = {}
    for r in rows:
        k = (r.period_end, r.period_type, series(r.value_basis))
        if k not in best or r.revision > best[k].revision:
            best[k] = r
    return sorted((_period(r) for r in best.values()),
                  key=lambda p: (p["period_end"], p["period_type"], p["value_basis"]))


def profile(db: Session, symbol: str, ts: datetime) -> CompanyProfile | None:
    return db.scalar(select(CompanyProfile).where(CompanyProfile.symbol == symbol,
                                                  CompanyProfile.available_at <= ts))


def peers(db: Session, sector: str, exclude: str, ts: datetime) -> list[str]:
    return list(db.scalars(select(CompanyProfile.symbol).where(
        CompanyProfile.sector == sector, CompanyProfile.symbol != exclude,
        CompanyProfile.is_index.is_(False), CompanyProfile.available_at <= ts)).all())


def actions(db: Session, symbol: str, ts: datetime) -> list[CorporateAction]:
    return list(db.scalars(select(CorporateAction).where(
        CorporateAction.symbol == symbol, CorporateAction.available_at <= ts,
        CorporateAction.announced_at <= ts).order_by(CorporateAction.ex_date)).all())
