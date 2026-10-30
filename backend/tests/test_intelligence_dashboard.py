"""AI Intelligence Dashboard (work package O): shape, NO_DATA and account isolation."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal as D

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import Account, Order, Position
from app.models.user import UserPreferences
from tests.test_phase6_execution import buy, fake, h, positions  # noqa: F401

API = "/api/v1"
SECTIONS = {"regime", "models", "forecast", "calibration", "prediction_history",
            "strategy_performance", "backtests", "account_results", "risk", "data_freshness",
            "broker", "drift", "error_attribution", "no_trade"}


def _uid(client, h):  # noqa: F811
    return uuid.UUID(client.get(f"{API}/users/me", headers=h).json()["id"])


def _dash(client, h, **q):  # noqa: F811
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = client.get(f"{API}/intelligence/dashboard?{qs}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_dashboard_shape_and_no_data_sections(client, h):  # noqa: F811
    d = _dash(client, h, underlying="NIFTY")
    assert d["account_mode"] == "DEMO" and d["account_label"] == "DEMO_VIRTUAL"
    assert d["is_demo_data"] is True and d["data_source"] == "DEMO"
    assert set(d["sections"]) == SECTIONS
    for name, s in d["sections"].items():
        assert s["status"] in ("OK", "NO_DATA", "UNAVAILABLE"), name
        assert s["source"], name
        if s["status"] != "OK":
            assert s["reason"], name
    sec = d["sections"]
    assert sec["regime"]["status"] == "OK" and sec["regime"]["data_label"] == "SIMULATED"
    assert sec["regime"]["data"]["calibration_status"] == "UNCALIBRATED"
    for name in ("models", "forecast", "calibration", "drift", "error_attribution",
                 "prediction_history", "strategy_performance", "account_results", "no_trade"):
        assert sec[name]["status"] == "NO_DATA", name
    assert sec["forecast"]["data"]["probabilities"] is None
    assert sec["backtests"]["status"] == "UNAVAILABLE" and sec["backtests"]["link"]
    assert sec["broker"]["status"] == "UNAVAILABLE"
    assert sec["broker"]["data"]["real_broker_connected"] is False
    assert sec["risk"]["data"]["new_entries_allowed"] is True


def test_dashboard_shows_latest_decision_and_history(client, h):  # noqa: F811
    rep = client.get(f"{API}/decision/NIFTY", headers=h).json()
    sec = _dash(client, h, underlying="NIFTY")["sections"]
    assert sec["no_trade"]["status"] == "OK"
    assert sec["no_trade"]["data"]["decision"] == rep["decision"]
    assert sec["no_trade"]["data"]["reasons"] == (rep["no_trade_reasons"]
                                                  + rep["watch_reasons"])[:20]
    assert sec["prediction_history"]["status"] == "OK"
    assert sec["prediction_history"]["data"][0]["origin"] == "DECISION"


def _real_closed(db: Session, uid: uuid.UUID, net: D) -> None:
    acc = Account(user_id=uid, mode="REAL", status="ACTIVE", broker="TESTBROKER",
                  opening_balance=None)
    db.add(acc)
    db.flush()
    now = utcnow()
    common = dict(user_id=uid, account_id=acc.id, account_mode="REAL", underlying="NIFTY",
                  trading_symbol="REAL-NIFTY-24000-CE", expiry=date.today() + timedelta(days=3),
                  strike=D("24000"), option_type="CE", lot_size=75, data_source="LIVE")
    o = Order(**common, client_order_id=f"REAL-{uuid.uuid4().hex[:12]}", broker="TESTBROKER",
              mode="LIVE", purpose="ENTRY", side="BUY", lot_size_source="INSTRUMENT_MASTER",
              quantity=75, order_type="MARKET", status="FILLED", filled_qty=75,
              avg_fill_price=D("10"), risk_snapshot={}, created_at=now, updated_at=now)
    db.add(o)
    db.flush()
    db.add(Position(**common, entry_order_id=o.id, mode="LIVE", quantity=0, entry_qty=75,
                    avg_entry=D("10"), avg_exit=D("100"), exit_qty=75, realized_gross=net,
                    charges_total=D("0"), status="CLOSED", opened_at=now, closed_at=now,
                    exit_reason="MANUAL"))
    db.commit()


def test_real_numbers_never_mix_into_demo(client, h, fake, engine):  # noqa: F811
    buy(client, h)
    pid = positions(client, h)[0]["id"]
    assert client.post(f"{API}/positions/{pid}/exit", headers=h).status_code == 200
    uid = _uid(client, h)
    with Session(engine) as db:
        _real_closed(db, uid, D("99999"))
    sec = _dash(client, h, underlying="NIFTY")["sections"]
    res = sec["account_results"]
    assert res["account_mode"] == "DEMO" and res["label"] == "DEMO_VIRTUAL"
    assert res["data"]["closed_trades"] == 1
    assert D(res["data"]["realized_net"]) < 100  # the REAL ₹99,999 is excluded
    strat = sec["strategy_performance"]
    assert [s["strategy"] for s in strat["data"]] == ["manual"]
    assert strat["data"][0]["trades"] == 1
    with Session(engine) as db:
        db.get(UserPreferences, uid).active_account_mode = "REAL"
        db.commit()
    real = _dash(client, h, underlying="NIFTY")
    assert real["account_mode"] == "REAL" and real["account_label"] == "REAL"
    rres = real["sections"]["account_results"]
    assert rres["data"]["closed_trades"] == 1
    assert D(rres["data"]["realized_net"]) == D("99999")
    assert real["sections"]["risk"]["data"]["new_entries_allowed"] is False
