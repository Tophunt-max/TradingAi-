from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import ModelVersion, Prediction, User, UserRole
from tests.conftest import auth_header, register_and_verify


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


def test_full_analysis_payload(client, h):
    r = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["decision"] == "NO_TRADE" and b["source"] == "DEMO"
    assert len(b["pipeline"]) == 12 and b["regime"]["label"]
    assert {"vwap", "supertrend", "adx14", "stoch_k", "pivots", "fibonacci"} <= set(b["indicators"])
    assert {s["id"] for s in b["strategies"]} >= {"momentum", "smc", "ai_adaptive"}
    assert b["prediction"] is None and b["probabilities"] is None
    assert set(b["smc"]) >= {"structure_events", "order_blocks", "fair_value_gaps"}
    assert all("bars_ago" in p for p in b["patterns"])
    assert b["explanation"][-1] == "Decision: NO TRADE."
    # Phase 2 contract still present for older clients.
    assert {"trend", "signals", "support", "resistance", "abstain_reasons"} <= set(b)


def test_multi_timeframe(client, h):
    b = client.get("/api/v1/analysis/BANKNIFTY/mtf", headers=h).json()
    assert [v["timeframe"] for v in b["views"]] == ["5m", "15m", "1h", "1d"]
    assert all(v["available"] for v in b["views"])
    assert b["alignment"] in ("ALIGNED_BULLISH", "ALIGNED_BEARISH", "MIXED")
    assert client.get("/api/v1/analysis/NOPE/mtf", headers=h).status_code == 404


def test_chart_annotations_and_new_indicators(client, h):
    r = client.get("/api/v1/charts/NIFTY/candles?timeframe=5m&limit=100"
                   "&indicators=vwap,supertrend,adx14,stoch,cci20,rvol20&annotations=true",
                   headers=h)
    b = r.json()
    for k in ("vwap", "supertrend", "supertrend_dir", "adx14", "stoch_k", "cci20", "rvol20"):
        assert len(b["indicators"][k]) == 100
    first = b["candles"][0]["ts"]
    ann = b["annotations"]
    assert set(ann) == {"patterns", "structure", "zones"}
    assert all(e["confirmed_ts"] >= first for e in ann["patterns"])
    assert all(z["top"] >= z["bottom"] for z in ann["zones"])
    plain = client.get("/api/v1/charts/NIFTY/candles", headers=h).json()
    assert plain["annotations"] is None


def test_training_on_demo_is_rejected_and_cannot_be_promoted(client, h, admin):
    assert client.post("/api/v1/admin/models/train", headers=h,
                       json={"symbol": "NIFTY", "timeframe": "15m"}).status_code == 403
    r = client.post("/api/v1/admin/models/train", headers=admin,
                    json={"symbol": "NIFTY", "timeframe": "15m"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["status"] == "REJECTED" and m["data_source"] == "DEMO"
    assert m["metrics"]["oos_samples"] >= 300 and m["metrics"]["calibration_curve"]
    data = next(c for c in m["gate"]["checks"] if c["key"] == "data")
    assert data["passed"] is False
    r = client.post(f"/api/v1/admin/models/{m['id']}/promote", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_GATE_FAILED"
    listed = client.get("/api/v1/models", headers=h).json()
    assert [x["id"] for x in listed] == [m["id"]]
    assert client.get(f"/api/v1/models/{m['id']}", headers=h).json()["status"] == "REJECTED"


def test_promoted_model_serves_probabilities_and_records_once(client, admin, engine):
    m = client.post("/api/v1/admin/models/train", headers=admin,
                    json={"symbol": "NIFTY", "timeframe": "15m"}).json()
    # Simulate a model that passed the gate on this data source (the API refuses
    # DEMO promotion, so set it directly to test the serving path).
    with Session(engine) as db:
        db.execute(update(ModelVersion).where(ModelVersion.id == uuid.UUID(m["id"]))
                   .values(status="PROMOTED"))
        db.commit()
    b = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=admin).json()
    p = b["probabilities"]
    assert p and abs(sum(p.values()) - 1) < 1e-3
    assert b["prediction"]["uncertainty"] is not None
    assert b["decision"] == "NO_TRADE"
    step = next(s for s in b["pipeline"] if s["key"] == "prediction")
    assert step["status"] == "DONE"
    ai = next(s for s in b["strategies"] if s["id"] == "ai_adaptive")
    assert ai["direction"] != "UNAVAILABLE"
    client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=admin)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Prediction)) == 1
    hist = client.get("/api/v1/models/predictions?symbol=NIFTY", headers=admin).json()
    assert len(hist) == 1 and hist[0]["outcome"] is None
    # Other timeframes have no model.
    other = client.get("/api/v1/analysis/NIFTY?timeframe=1h", headers=admin).json()
    assert other["probabilities"] is None


def test_model_trained_on_other_source_abstains(client, admin, engine):
    m = client.post("/api/v1/admin/models/train", headers=admin,
                    json={"symbol": "NIFTY", "timeframe": "15m"}).json()
    with Session(engine) as db:
        db.execute(update(ModelVersion).where(ModelVersion.id == uuid.UUID(m["id"]))
                   .values(status="PROMOTED", data_source="LIVE"))
        db.commit()
    b = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=admin).json()
    assert b["probabilities"] is None
    assert "trained on LIVE data" in b["prediction"]["reason"]


def test_retire(client, admin, engine):
    m = client.post("/api/v1/admin/models/train", headers=admin,
                    json={"symbol": "NIFTY", "timeframe": "15m"}).json()
    with Session(engine) as db:
        db.execute(update(ModelVersion).where(ModelVersion.id == uuid.UUID(m["id"]))
                   .values(status="PROMOTED"))
        db.commit()
    r = client.post(f"/api/v1/admin/models/{m['id']}/retire", headers=admin)
    assert r.json()["status"] == "RETIRED"
    b = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=admin).json()
    assert b["prediction"] is None
