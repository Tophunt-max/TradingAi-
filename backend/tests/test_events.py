"""News & events (work package H). All events here are FICTIONAL test fixtures."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.events import risk as RISK
from app.events import store
from app.market_data.provider import DemoProvider
from app.models import User, UserRole
from tests.conftest import auth_header, register_and_verify

TS = datetime(2025, 6, 10, 9, 0, tzinfo=UTC)
W_BEFORE, W_AFTER = timedelta(hours=2), timedelta(hours=1)


def ev(**over) -> dict:
    e = {"id": uuid.uuid4(), "category": "EARNINGS", "scope": "INSTRUMENT",
         "symbols": ["TESTCO"], "title": "TESTCO quarterly results (fictional)",
         "source": "TEST_FIXTURE", "source_reliability": "OFFICIAL",
         "event_time": TS + timedelta(minutes=30), "published_at": TS - timedelta(days=3),
         "available_at": TS - timedelta(days=3), "importance": "HIGH", "novelty": "NEW",
         "supersedes_id": None}
    e.update(over)
    return e


def gate(events, symbol="TESTCO", underlying=None, ts=TS, sector=None):
    return RISK.event_risk(events, symbol, underlying, ts, W_BEFORE, W_AFTER, sector)


def test_gate_levels_and_actions():
    assert gate([ev()])["level"] == "HIGH" and gate([ev()])["action"] == "NO_TRADE"
    g = gate([ev(importance="MEDIUM")])
    assert (g["level"], g["action"]) == ("ELEVATED", "REDUCE_EXPOSURE")
    g = gate([ev(importance="LOW")])
    assert (g["level"], g["action"]) == ("NONE", "NONE") and len(g["events"]) == 1
    none = gate([])
    assert none["level"] == "NONE" and "absence of data is not absence of risk" in \
        none["reasons"][0]
    # Outside the window: 3 h ahead, or 2 h ago.
    assert gate([ev(event_time=TS + timedelta(hours=3))])["level"] == "NONE"
    assert gate([ev(event_time=TS - timedelta(hours=2))])["level"] == "NONE"
    assert gate([ev(event_time=TS - timedelta(minutes=30))])["level"] == "HIGH"


def test_gate_relevance_by_scope():
    market = ev(scope="MARKET", symbols=[], category="MONETARY_POLICY")
    assert gate([market], symbol="ANYCO")["action"] == "NO_TRADE"
    idx = ev(scope="INDEX", symbols=["NIFTY"], importance="MEDIUM")
    assert gate([idx], symbol="NIFTY")["level"] == "ELEVATED"
    assert gate([idx], symbol="TESTCO", underlying="NIFTY")["level"] == "ELEVATED"
    assert gate([idx], symbol="BANKNIFTY")["level"] == "NONE"
    sector = ev(scope="SECTOR", symbols=["FICTIONAL_SECTOR"])
    assert gate([sector], symbol="TESTCO", sector="FICTIONAL_SECTOR")["level"] == "HIGH"
    assert gate([sector], symbol="TESTCO")["level"] == "NONE"


def test_gate_has_no_lookahead_and_handles_supersession():
    # Published/ingested after the decision time: invisible.
    late = ev(available_at=TS + timedelta(minutes=1), published_at=TS)
    assert gate([late])["level"] == "NONE"
    assert gate([late], ts=TS + timedelta(minutes=2))["level"] == "HIGH"
    # A downgrade (UPDATE) replaces the original only once the update is available.
    orig = ev(importance="HIGH")
    upd = ev(importance="LOW", novelty="UPDATE", supersedes_id=orig["id"],
             available_at=TS + timedelta(minutes=10))
    assert gate([orig, upd])["level"] == "HIGH"
    assert gate([orig, upd], ts=TS + timedelta(minutes=11))["level"] == "NONE"
    dup = ev(novelty="DUPLICATE", supersedes_id=orig["id"])
    assert len(gate([orig, dup])["events"]) == 1


def payload(**over) -> dict:
    e = {"category": "EARNINGS", "scope": "INSTRUMENT", "symbols": ["testco"],
         "title": "TESTCO   Quarterly results!", "source": "TEST_FIXTURE",
         "source_reliability": "OFFICIAL", "event_time": "2025-06-10T09:30:00Z",
         "published_at": "2025-06-07T09:00:00Z", "available_at": "2025-06-07T09:05:00Z",
         "importance": "HIGH"}
    e.update(over)
    return e


def test_novelty_new_duplicate_update(engine):
    with Session(engine) as db:
        r1 = store.import_events(db, store.EventsImport.model_validate({"events": [payload()]}))
        r2 = store.import_events(db, store.EventsImport.model_validate(
            {"events": [payload(title="testco quarterly results")]}))   # normalised: same
        r3 = store.import_events(db, store.EventsImport.model_validate(
            {"events": [payload(summary="Board meeting moved to the afternoon.",
                                importance="MEDIUM")]}))
        db.commit()
        assert (r1["NEW"], r2["DUPLICATE"], r3["UPDATE"]) == (1, 1, 1)
        rows = store.query(db, ts=datetime(2025, 6, 10, tzinfo=UTC))
        assert len(rows) == 1 and rows[0]["importance"] == "MEDIUM"
        assert str(rows[0]["supersedes_id"]) == r1["ids"][0]
        allrows = store.query(db, ts=datetime(2025, 6, 10, tzinfo=UTC), include_duplicates=True)
        assert {r["novelty"] for r in allrows} == {"NEW", "DUPLICATE", "UPDATE"}


def test_import_validation():
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for bad, msg in ((payload(available_at=future), "future"),
                     (payload(published_at=future, available_at=None), "future"),
                     (payload(scope="INSTRUMENT", symbols=[]), "symbols"),
                     (payload(direction_hint="UP"), "uncertainty_note"),
                     (payload(event_time="2025-06-10T09:30:00"), "timezone"),
                     (payload(url="ftp://x"), "http")):
        with pytest.raises(ValidationError, match=msg):
            store.EventsImport.model_validate({"events": [bad]})
    ok = store.EventsImport.model_validate({"events": [payload(
        direction_hint="UP", uncertainty_note="Vendor's own estimate; unverified.")]})
    assert ok.events[0].symbols == ["TESTCO"]


def test_calendar_uses_provider_expiries_and_configured_holidays(monkeypatch):
    monkeypatch.setenv("MARKET_HOLIDAYS", "2026-11-02,bad-date")
    get_settings.cache_clear()
    now = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
    cal = store.calendar(get_settings(), DemoProvider(now=lambda: now), ("NIFTY",),
                         date(2026, 10, 29), days=30)
    exp = [c for c in cal if c["category"] == "EXPIRY"]
    assert exp and all(c["label"] == "SIMULATED" for c in exp)
    hol = [c for c in cal if c["category"] == "HOLIDAY"]
    assert [c["date"] for c in hol] == ["2026-11-02"] and hol[0]["label"] == "CONFIGURED"
    assert not any(c["category"] in ("MONETARY_POLICY", "EARNINGS") for c in cal)
    get_settings.cache_clear()


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


@pytest.fixture
def admin(client, otp, engine):
    tokens = register_and_verify(client, otp, "admin@example.com")
    with Session(engine) as db:
        db.execute(update(User).where(User.email == "admin@example.com")
                   .values(role=UserRole.ADMIN))
        db.commit()
    return auth_header(tokens)


def test_events_api_and_analysis_gate(client, h, admin):
    now = datetime.now(UTC)
    body = {"events": [payload(symbols=["NIFTY"], scope="INDEX", title="Fictional index event",
                               event_time=(now + timedelta(minutes=30)).isoformat(),
                               published_at=(now - timedelta(minutes=5)).isoformat(),
                               available_at=None)]}
    assert client.post("/api/v1/admin/events/import", headers=h, json=body).status_code == 403
    r = client.post("/api/v1/admin/events/import", headers=admin, json=body)
    assert r.status_code == 200 and r.json()["NEW"] == 1
    lst = client.get("/api/v1/events?symbol=NIFTY", headers=h).json()
    assert len(lst["events"]) == 1 and "never generated" in lst["note"]
    assert any(c["category"] == "EXPIRY" and c["label"] == "SIMULATED" for c in lst["calendar"])
    assert client.get("/api/v1/events?symbol=RELIANCE", headers=h).json()["events"] == []
    risk = client.get("/api/v1/events/risk?symbol=NIFTY", headers=h).json()
    assert risk["level"] == "HIGH" and risk["action"] == "NO_TRADE"
    a = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h).json()
    assert a["event_risk"]["action"] == "NO_TRADE" and a["decision"] == "NO_TRADE"
    assert any("Event-risk gate" in x for x in a["abstain_reasons"])
    other = client.get("/api/v1/analysis/BANKNIFTY?timeframe=15m", headers=h).json()
    assert other["event_risk"]["level"] == "NONE"
