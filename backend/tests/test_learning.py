"""Self-learning (work package N): decision records, outcomes, attribution, metrics,
drift, promotion policy, shadow stage and rollback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.ai import features as F
from app.core.errors import ConflictError
from app.learning import drift, metrics, promotion
from app.learning import stats as ST
from app.learning.attribution import attribute
from app.learning.outcomes import compute_outcome, horizon_elapsed, resolve_outcomes
from app.market_data.provider import DemoProvider
from app.models import AuditLog, DecisionRecord, ModelVersion, Prediction, User, UserRole
from tests.conftest import auth_header, register_and_verify

T0 = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)  # Wednesday 11:30 IST


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


# ------------------------------------------------------------- pure pieces


def candles(closes, highs=None, lows=None):
    t = datetime(2026, 1, 1, tzinfo=UTC)
    return [{"ts": t + timedelta(minutes=15 * i), "open": c, "close": c,
             "high": (highs or closes)[i], "low": (lows or closes)[i], "volume": 1}
            for i, c in enumerate(closes)]


def test_compute_outcome_uses_only_following_bars():
    cs = candles([100, 101, 103, 102, 104, 90], highs=[100, 101, 105, 102, 104, 90],
                 lows=[100, 99, 101, 100, 103, 90])
    o = compute_outcome(cs, 0, 4, atr=2.0, predicted="UP")
    assert o["realized_return"] == 0.04 and o["realized_direction"] == "UP"
    assert o["max_favorable_excursion"] == 5 and o["max_adverse_excursion"] == 1
    # Bar 5 (90) lies beyond the horizon and must not affect the result.
    assert o["threshold"] == 1.0 and o["late_direction"] is None
    down = compute_outcome(cs, 0, 4, atr=2.0, predicted="DOWN")
    assert down["max_favorable_excursion"] == 1 and down["max_adverse_excursion"] == 5


def test_horizon_elapsed():
    bar = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    assert not horizon_elapsed(bar, "15m", 4, bar + timedelta(minutes=74))
    assert horizon_elapsed(bar, "15m", 4, bar + timedelta(minutes=75))


@pytest.mark.parametrize(("d", "o", "links", "expected"), [
    ({"data_quality": "WARN"}, {}, {}, ["DATA_QUALITY"]),
    ({"predicted_direction": "UP"}, {"realized_direction": "DOWN", "threshold": 1.0,
                                     "max_favorable_excursion": 0.2}, {}, ["WRONG_DIRECTION"]),
    ({"predicted_direction": "UP"}, {"realized_direction": "SIDEWAYS", "threshold": 1.0,
                                     "max_favorable_excursion": 1.5}, {}, ["WRONG_TIMING"]),
    ({"predicted_direction": "DOWN"}, {"realized_direction": "UP", "threshold": 1.0,
                                       "max_favorable_excursion": 0.1,
                                       "late_direction": "DOWN"}, {}, ["WRONG_TIMING"]),
    ({"predicted_direction": "UP"}, {"realized_direction": "UP"}, {}, []),
    ({"regime": "RANGE"}, {"regime_at_resolution": "TRENDING_UP"}, {}, ["REGIME_CHANGE"]),
    ({"predicted_vol_range": {"low": 0.001, "high": 0.002}}, {"realized_volatility": 0.01}, {},
     ["VOLATILITY_ERROR"]),
    ({}, {"invalidated_patterns": ["Hammer"]}, {}, ["PATTERN_MISCLASSIFICATION"]),
    ({"predicted_direction": "UP"}, {"realized_direction": "UP", "realized_return": 0.004},
     {"premium_change": Decimal("-3.5")}, ["OPTION_PRICING"]),
    ({}, {}, {"slippage_pct": 2.5}, ["EXECUTION_SLIPPAGE"]),
    ({}, {}, {"slippage_pct": 0.4}, []),
    ({}, {}, {"order_status": "RECONCILIATION_REQUIRED"}, ["RISK_ORDER_MANAGEMENT"]),
])
def test_attribution_rules(d, o, links, expected):
    assert attribute(d, o, links, slippage_threshold_pct=1.0) == expected


def test_psi_thresholds_and_reference():
    rng = np.random.default_rng(0)
    ref = drift.reference_column(rng.normal(0, 1, 5000))
    assert drift.psi(ref, rng.normal(0, 1, 2000)) < drift.PSI_MODERATE
    shifted = drift.psi(ref, rng.normal(1.5, 1, 2000))
    assert shifted > drift.PSI_SIGNIFICANT and drift.psi_level(shifted) == "SIGNIFICANT"
    assert drift.psi_level(0.15) == "MODERATE" and drift.psi_level(0.05) == "STABLE"
    assert drift.categorical_psi({"UP": 0.5, "DOWN": 0.5}, {"UP": 0.5, "DOWN": 0.5}) == 0


def test_performance_drift_needs_minimum_samples():
    few = drift.performance_drift([2.0, 2.0], 0.6)          # two big losses
    assert few["status"] == "INSUFFICIENT_DATA"
    many_bad = drift.performance_drift([0.9] * 150, 0.6)
    assert many_bad["status"] == "DEGRADED"
    assert drift.performance_drift([0.62] * 150, 0.6)["status"] == "STABLE"


def calibrated_predictions(n: int, classes=ST.CLASSES):
    """Fictional forecasts that are right exactly 70 % of the time at 70 % confidence."""
    P, y = [], []
    k = len(classes)
    for i in range(n):
        pred = i % k
        actual = pred if i % 10 < 7 else (pred + 1) % k
        row = [(1 - 0.7) / (k - 1)] * k
        row[pred] = 0.7
        P.append(row)
        y.append(actual)
    return np.array(P), np.array(y)


def test_bootstrap_ci_and_calibration_table():
    P, y = calibrated_predictions(300)
    B = np.tile([1 / 3] * 3, (300, 1))
    boot = ST.improvement_vs_baseline(P, B, y, block=4)
    assert boot["log_loss_improvement"]["lo"] > 0 and boot["brier_improvement"]["lo"] > 0
    worse = ST.improvement_vs_baseline(B, P, y, block=4)
    assert worse["log_loss_improvement"]["hi"] < 0
    ece, table = ST.calibration_table(P, y)
    assert ece < 0.01 and table[0]["predicted"] == 0.7 and table[0]["realized"] == 0.7


# ---------------------------------------------------- decision records & outcomes


def _demo_record(db, bar_index_from_end=10, **over) -> DecisionRecord:
    cs = DemoProvider(now=lambda: T0).candles("NIFTY", "15m", 300).data
    c = cs[-bar_index_from_end]
    rec = DecisionRecord(origin="ANALYSIS", origin_ref="", decision_ts=c["ts"] + timedelta(
        minutes=16), bar_ts=c["ts"], symbol="NIFTY", timeframe="15m", horizon_bars=4,
        close=Decimal(str(c["close"])), atr=None, data_quality="OK", data_source="DEMO",
        calibration_status="UNCALIBRATED", decision="NO_TRADE", predicted_direction="UP",
        no_trade_reasons=["test"], strategies=[], evidence={}, **over)
    db.add(rec)
    db.flush()
    return rec


def test_resolver_never_touches_records_before_horizon(engine):
    with Session(engine) as db:
        rec = _demo_record(db, bar_index_from_end=3)    # only 2 newer bars exist at T0
        early = resolve_outcomes(db, DemoProvider(now=lambda: T0), T0)
        assert rec.outcome_status == "PENDING" and early["resolved"] == 0
        later = T0 + timedelta(hours=2)
        stats = resolve_outcomes(db, DemoProvider(now=lambda: later), later)
        assert stats["resolved"] == 1 and rec.outcome_status == "RESOLVED"
        cs = DemoProvider(now=lambda: later).candles("NIFTY", "15m", 300).data
        t = next(i for i, c in enumerate(cs) if c["ts"] == rec.bar_ts)
        assert rec.realized_return == round(cs[t + 4]["close"] / cs[t]["close"] - 1, 6)
        assert rec.realized_direction in ("UP", "DOWN", "SIDEWAYS")
        assert rec.max_favorable_excursion >= 0 and rec.regime_at_resolution is not None
        if rec.realized_direction != "UP":
            assert {"WRONG_DIRECTION", "WRONG_TIMING"} & set(rec.error_classes)
        # Resolving again changes nothing.
        assert resolve_outcomes(db, DemoProvider(now=lambda: later), later)["resolved"] == 0


def test_old_decisions_outside_history_are_unresolvable(engine):
    with Session(engine) as db:
        rec = DecisionRecord(origin="ANALYSIS", origin_ref="", decision_ts=T0 - timedelta(
            days=365), bar_ts=T0 - timedelta(days=365), symbol="NIFTY", timeframe="15m",
            horizon_bars=4, close=Decimal("1"), data_quality="OK", data_source="DEMO",
            calibration_status="UNCALIBRATED", decision="NO_TRADE")
        db.add(rec)
        db.flush()
        resolve_outcomes(db, DemoProvider(now=lambda: T0), T0)
        assert rec.outcome_status == "UNRESOLVABLE"


def test_analysis_records_every_decision_once_per_bar(client, h, engine):
    a = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h).json()
    assert a["decision"] == "NO_TRADE" and a["decision_record_id"]
    client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h)
    client.get("/api/v1/analysis/NIFTY?timeframe=1h", headers=h)
    with Session(engine) as db:
        rows = db.scalars(select(DecisionRecord)).all()
        assert len(rows) == 2 and {r.timeframe for r in rows} == {"15m", "1h"}
        r = next(x for x in rows if x.timeframe == "15m")
        assert r.decision in ("NO_TRADE", "SETUP_CANDIDATE") and r.no_trade_reasons
        assert r.probabilities is None and r.calibration_status == "UNCALIBRATED"
        assert r.indicator_version and r.data_source == "DEMO" and r.horizon_bars == 4
        assert r.event_risk == "NONE"
    page = client.get("/api/v1/learning/decisions?limit=1", headers=h).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["outcome_status"] == "PENDING"
    nt = client.get("/api/v1/learning/decisions?decision=NO_TRADE", headers=h).json()
    assert all(i["decision"] == "NO_TRADE" for i in nt["items"])


def test_bot_evaluations_are_recorded(client, h, engine):
    from tests.test_phase5_bots import NOW, create, start, tick

    bot = create(client, h)
    start(client, h, bot)
    assert tick(engine, NOW) == {"SIGNAL": 1}
    tick(engine, NOW)                                   # same bar: no second record
    tick(engine, NOW + timedelta(minutes=5))            # cooldown → SKIPPED
    with Session(engine) as db:
        rows = db.scalars(select(DecisionRecord).where(DecisionRecord.origin == "BOT")
                          .order_by(DecisionRecord.bar_ts)).all()
        assert [r.decision for r in rows] == ["SIGNAL", "SKIPPED"]
        assert rows[0].origin_ref == uuid.UUID(bot["id"]).hex
        assert rows[0].predicted_direction == "UP" and rows[1].no_trade_reasons


def test_summary_metrics_and_no_trade_analysis():
    P, y = calibrated_predictions(60)

    class R:
        def __init__(self, i):
            self.outcome_status = "RESOLVED"
            self.probabilities = dict(zip(ST.CLASSES, P[i].tolist(), strict=True))
            self.predicted_direction = ST.CLASSES[int(P[i].argmax())]
            self.realized_direction = ST.CLASSES[int(y[i])]
            self.timeframe, self.regime, self.strategies = "15m", "RANGE", ["momentum"]
            self.decision = "NO_TRADE" if i % 2 else "SETUP_CANDIDATE"
            self.error_classes = [] if self.predicted_direction == self.realized_direction \
                else ["WRONG_DIRECTION"]
            self.realized_return = 0.001 * (1 if i % 3 else -1)
            self.plan = {"side": "CE"} if i % 4 == 1 else None

    s = metrics.summary([R(i) for i in range(60)])
    assert s["resolved"] == 60 and s["probabilistic"]["samples"] == 60
    assert s["probabilistic"]["ece"] < 0.01
    assert s["accuracy"]["overall"]["accuracy"] == 0.7
    assert s["accuracy"]["by_strategy"]["momentum"]["samples"] == 60
    assert s["error_classes"]["WRONG_DIRECTION"] == 18
    nt = s["no_trade"]
    assert nt["label"] == "HYPOTHETICAL" and nt["samples"] == 30
    assert nt["counterfactual_setup_samples"] == 15


# --------------------------------------------------------- promotion policy


def model(db, status="CANDIDATE", oos=600, ece=0.02, lo=0.01, source="LIVE", **over):
    mv = ModelVersion(
        name="NIFTY 15m +4 bars (test)", symbol="NIFTY", timeframe="15m", horizon_bars=4,
        algorithm="test", feature_version=F.FEATURE_VERSION, hyperparams={}, params={},
        metrics={"oos_samples": oos, "ece": ece, "brier": 0.6, "log_loss": 1.0,
                 "baseline_log_loss": 1.1, "baseline_brier": 0.66,
                 "class_share": {c: 1 / 3 for c in ST.CLASSES},
                 "bootstrap": {"n": oos, "block": 4, "resamples": 1000,
                               "log_loss_improvement": {"mean": lo + 0.01, "lo": lo,
                                                        "hi": lo + 0.02},
                               "brier_improvement": {"mean": lo + 0.01, "lo": lo,
                                                     "hi": lo + 0.02}}},
        gate={"passed": True, "checks": []}, data_source=source, data_provider="TEST",
        train_rows=2000, data_from=T0 - timedelta(days=60), data_to=T0 - timedelta(days=1),
        status=status, **over)
    db.add(mv)
    db.flush()
    return mv


def add_shadow_predictions(db, mv, n, source="LIVE", good=True):
    P, y = calibrated_predictions(n)
    if not good:
        P = np.roll(P, 1, axis=1)                      # confidently wrong
    for i in range(n):
        db.add(Prediction(model_version_id=mv.id, symbol=mv.symbol, timeframe=mv.timeframe,
                          horizon_bars=4, bar_ts=T0 + timedelta(minutes=15 * i),
                          close=Decimal("100"), p_down=P[i][0], p_sideways=P[i][1],
                          p_up=P[i][2], data_source=source, outcome=ST.CLASSES[y[i]],
                          resolved_at=T0, created_at=T0 + timedelta(minutes=15 * i)))
    db.flush()


def test_offline_criteria_each_enforced(engine):
    with Session(engine) as db:
        for over, key in (({"oos": 499}, "oos_samples"), ({"ece": 0.08}, "offline_ece"),
                          ({"lo": -0.001}, "offline_log_loss_improvement"),
                          ({"source": "DEMO"}, "data")):
            mv = model(db, **over)
            failed = {c["key"] for c in promotion.offline_checks(mv) if not c["passed"]}
            assert key in failed
            with pytest.raises(ConflictError) as e:
                promotion.to_shadow(db, mv)
            assert e.value.code == "MODEL_GATE_FAILED"
        mv = model(db)
        del mv.metrics["bootstrap"]
        assert not all(c["passed"] for c in promotion.offline_checks(mv))


def test_shadow_stage_required_and_validated(engine):
    with Session(engine) as db:
        mv = model(db)
        with pytest.raises(ConflictError) as e:
            promotion.promote(db, mv)
        assert e.value.code == "MODEL_SHADOW_REQUIRED"
        promotion.to_shadow(db, mv, now=T0)
        assert mv.status == "SHADOW"
        add_shadow_predictions(db, mv, 150)
        with pytest.raises(ConflictError) as e:
            promotion.promote(db, mv)                # 150 < 200 resolved
        assert e.value.code == "MODEL_SHADOW_FAILED"
        mv2 = model(db)
        promotion.to_shadow(db, mv2, now=T0)
        add_shadow_predictions(db, mv2, 240, good=False)
        ev = promotion.shadow_evaluation(mv2, promotion.shadow_predictions(db, mv2))
        assert not ev["passed"]
        mv3 = model(db)
        promotion.to_shadow(db, mv3, now=T0)
        add_shadow_predictions(db, mv3, 240, source="DEMO")
        ev3 = promotion.shadow_evaluation(mv3, promotion.shadow_predictions(db, mv3))
        assert not next(c for c in ev3["checks"] if c["key"] == "shadow_data")["passed"]
        mv4 = model(db)
        promotion.to_shadow(db, mv4, now=T0)
        add_shadow_predictions(db, mv4, 240)
        ev4 = promotion.shadow_evaluation(mv4, promotion.shadow_predictions(db, mv4))
        assert ev4["passed"], ev4["checks"]
        promotion.promote(db, mv4)
        assert mv4.status == "PROMOTED" and mv4.promoted_at is not None


def test_binary_model_shadow_scoring(engine):
    with Session(engine) as db:
        mv = model(db)
        mv.metrics = {**mv.metrics, "target": "binary",
                      "class_share": {"DOWN": 0.5, "UP": 0.5}}
        promotion.to_shadow(db, mv, now=T0)
        P, y = calibrated_predictions(240, ST.BINARY_CLASSES)
        for i in range(240):
            db.add(Prediction(model_version_id=mv.id, symbol="NIFTY", timeframe="15m",
                              horizon_bars=4, bar_ts=T0 + timedelta(minutes=15 * i),
                              close=Decimal("1"), p_down=P[i][0], p_sideways=0.0,
                              p_up=P[i][1], data_source="LIVE",
                              outcome=ST.BINARY_CLASSES[y[i]], created_at=T0))
        db.flush()
        ev = promotion.shadow_evaluation(mv, promotion.shadow_predictions(db, mv))
        assert ev["metrics"]["samples"] == 240 and ev["passed"], ev["checks"]


def test_api_shadow_promote_rollback_with_audit(client, admin, engine):
    with Session(engine) as db:
        old = model(db, status="RETIRED", promoted_at=T0 - timedelta(days=10),
                    retired_at=T0 - timedelta(days=1))
        cur = model(db)
        db.commit()
        old_id, cur_id = str(old.id), str(cur.id)
    r = client.post(f"/api/v1/admin/models/{cur_id}/promote", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_SHADOW_REQUIRED"
    r = client.post(f"/api/v1/admin/models/{cur_id}/shadow", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "SHADOW"
    with Session(engine) as db:
        mv = db.get(ModelVersion, uuid.UUID(cur_id))
        mv.shadow_started_at = T0
        add_shadow_predictions(db, mv, 220)
        db.commit()
    st = client.get(f"/api/v1/models/{cur_id}/promotion", headers=admin).json()
    assert st["offline"]["passed"] and st["shadow"]["passed"]
    r = client.post(f"/api/v1/admin/models/{cur_id}/promote", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "PROMOTED"
    r = client.post(f"/api/v1/admin/models/{cur_id}/rollback", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == old_id and r.json()["status"] == "PROMOTED"
    assert client.get(f"/api/v1/models/{cur_id}", headers=admin).json()["status"] == \
        "ROLLED_BACK"
    r = client.post(f"/api/v1/admin/models/{old_id}/rollback", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_PREVIOUS_MODEL"
    with Session(engine) as db:
        logs = db.scalars(select(AuditLog.action).where(
            AuditLog.action.like("model.%"))).all()
        assert {"model.shadow_started", "model.promoted", "model.rolled_back"} <= set(logs)


# ----------------------------------------------------------------- drift


def _promoted_with_reference(db, shift=0.0):
    mv = model(db, status="PROMOTED", promoted_at=T0)
    provider = DemoProvider()
    X, _ = drift.recent_features(provider, mv)
    mv.metrics = {**mv.metrics, "feature_reference": drift.reference_from_matrix(
        X + shift, F.FEATURE_NAMES)}
    db.flush()
    return mv, provider


def test_few_losses_do_not_demote(engine):
    with Session(engine) as db:
        mv, provider = _promoted_with_reference(db)
        for i in range(2):                          # two confidently wrong outcomes
            db.add(Prediction(model_version_id=mv.id, symbol="NIFTY", timeframe="15m",
                              horizon_bars=4, bar_ts=T0 + timedelta(minutes=i),
                              close=Decimal("1"), p_down=0.05, p_sideways=0.05, p_up=0.9,
                              data_source="LIVE", outcome="DOWN"))
        db.flush()
        rep = drift.compute_report(db, provider, mv, datetime.now(UTC))
        assert rep.performance["status"] == "INSUFFICIENT_DATA"
        assert rep.status in ("STABLE", "MODERATE") and mv.status == "PROMOTED"


def test_significant_drift_degrades_and_analysis_abstains(client, h, engine):
    with Session(engine) as db:
        mv, provider = _promoted_with_reference(db, shift=10.0)
        rep = drift.compute_report(db, provider, mv, datetime.now(UTC))
        db.commit()
        assert rep.status == "DEGRADED" and rep.max_feature_psi > drift.PSI_SIGNIFICANT
    a = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h).json()
    assert a["probabilities"] is None and "DEGRADED" in a["prediction"]["reason"]
    d = client.get("/api/v1/learning/drift", headers=h).json()
    assert d["models"][0]["status"] == "DEGRADED"
    s = client.get("/api/v1/learning/summary", headers=h).json()
    assert s["drift"][0]["status"] == "DEGRADED" and "error_classes" in s
    assert s["by_decision"]


def test_worker_pass_and_admin_endpoint(client, h, admin, engine):
    client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h)
    assert client.post("/api/v1/admin/learning/run", headers=h).status_code == 403
    r = client.post("/api/v1/admin/learning/run", headers=admin)
    assert r.status_code == 200 and r.json()["resolved"] == 0   # horizon not elapsed yet
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(DecisionRecord)) == 1
        from app.core.config import get_settings
        from app.workers.learning import run_once

        later = datetime.now(UTC) + timedelta(hours=2)
        stats = run_once(db, get_settings(), DemoProvider(now=lambda: later), later)
        assert stats["resolved"] == 1
