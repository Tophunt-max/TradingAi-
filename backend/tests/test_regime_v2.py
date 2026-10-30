"""Regime detection v2 and Strategy Router v2 (work package J)."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from app.analysis import context as C
from app.analysis import pipeline
from app.analysis import regime as R
from app.analysis import smc as SMC
from app.market_data import demo
from app.strategies import router as RT
from app.strategies.catalog import STRATEGIES
from tests.conftest import auth_header, register_and_verify
from tests.test_phase4_analysis import bars, trend_bars

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)


def _down(n: int = 200) -> list[dict]:
    rnd, p, rows = random.Random(4), 400.0, []
    for _ in range(n):
        c = p * (1 - 0.004 + rnd.uniform(-0.0005, 0.0005))
        rows.append((p, max(p, c) * 1.0008, min(p, c) * 0.9992, c))
        p = c
    return bars(rows)


def _calm(n: int = 200, seed: int = 3) -> list[tuple[float, float, float, float]]:
    rnd, p, rows = random.Random(seed), 100.0, []
    for _ in range(n):
        c = p + rnd.uniform(-0.3, 0.3)
        rows.append((p, max(p, c) + 0.2, min(p, c) - 0.2, c))
        p = c
    return rows


def test_trends_get_high_confidence_and_keep_legacy_family():
    up = R.detect_v2(C.build(trend_bars(200, 1.0)))
    assert up["state"] == "STRONG_UPTREND" and up["family"] == "TRENDING"
    assert up["legacy_label"] == "TRENDING_UP"
    assert sum(up["confidence"].values()) == pytest.approx(1.0, abs=1e-3)
    assert set(up["confidence"]) == set(R.STATES)
    assert up["calibration_status"] == "UNCALIBRATED" and up["no_trade"] is False
    assert "breadth" in up["unavailable"]
    down = R.detect_v2(C.build(_down()))
    assert down["state"] == "STRONG_DOWNTREND"
    # Legacy API is unchanged for backtesting and the bot engine.
    assert R.detect(C.build(trend_bars(200, 1.0)))["label"] == "TRENDING_UP"
    assert R.FAMILY["SQUEEZE"] == "RANGE"


def test_insufficient_data_is_unknown():
    out = R.detect_v2(C.build(trend_bars(20, 1.0)))
    assert out["state"] == "UNKNOWN" and out["no_trade"] and out["confidence"] == {}


def test_quality_failure_and_events_force_no_trade_states():
    ctx = C.build(trend_bars(200, 1.0))
    assert R.detect_v2(ctx, quality_status="FAIL")["state"] == "ABNORMAL"
    ev = R.detect_v2(ctx, event_risk_level="HIGH")
    assert ev["confidence"]["EVENT_UNCERTAINTY"] > ev["confidence"]["RANGE"]
    flat = R.detect_v2(C.build(bars(_calm())), event_risk_level="HIGH")
    assert flat["state"] == "EVENT_UNCERTAINTY" and flat["no_trade"]


def test_gap_beyond_atr_extremes_is_abnormal():
    rows = _calm()
    o, h, lo, c = rows[-1]
    rows[-1] = (o + 8, o + 8.5, o + 7.5, o + 8.2)  # gap ≈ 8 points vs ATR ≈ 0.6
    out = R.detect_v2(C.build(bars(rows)))
    assert out["features"]["abnormal_move"] is True
    assert out["state"] == "ABNORMAL" and out["no_trade"]


def test_conflicting_top_states_are_unknown(monkeypatch):
    def tie(f, e, q):
        return {k: (2.0 if k in ("RANGE", "BREAKOUT") else 0.0) for k in R.STATES}
    monkeypatch.setattr(R, "state_scores", tie)
    out = R.detect_v2(C.build(trend_bars(200, 1.0)))
    assert out["state"] == "UNKNOWN" and out["margin"] < R.MIN_MARGIN
    assert any("Conflicting" in r for r in out["reasons"])


def test_low_confidence_is_unknown(monkeypatch):
    monkeypatch.setattr(R, "state_scores", lambda f, e, q: {k: 0.0 for k in R.STATES})
    out = R.detect_v2(C.build(trend_bars(200, 1.0)))
    assert out["state"] == "UNKNOWN"
    assert max(out["confidence"].values()) < R.MIN_CONFIDENCE


def test_v2_is_causal_and_uses_only_confirmed_structure():
    rows = demo.candles("NIFTY", "15m", NOW, 300)
    full, part = C.build(rows), C.build(rows[:250])
    a = R.detect_v2(full, 249, smc=SMC.analyse(full))
    b = R.detect_v2(part, 249, smc=SMC.analyse(part))
    assert a["confidence"] == b["confidence"] and a["state"] == b["state"]


# ----------------------------------------------------------------- router


def _reg(state: str, conf: dict[str, float]) -> dict:
    full = {s: 0.0 for s in R.STATES} | conf
    return {"state": state, "confidence": full, "calibration_status": "UNCALIBRATED"}


def test_expected_compatibility_is_probability_weighted():
    reg = _reg("STRONG_UPTREND", {"STRONG_UPTREND": 0.6, "RANGE": 0.4})
    out = {s["id"]: s for s in RT.route(reg)["strategies"]}
    m = RT.COMPAT["momentum"]
    assert out["momentum"]["expected_compatibility"] == pytest.approx(
        0.6 * m["STRONG_UPTREND"] + 0.4 * m["RANGE"])
    assert out["momentum"]["verdict"] == "REQUIRES_VALIDATION"
    assert out["reversal"]["verdict"] == "INCOMPATIBLE"
    assert out["dca_grid"]["verdict"] == "NO_TRADE"
    assert out["ai_adaptive"]["verdict"] == "NO_TRADE"
    assert set(out) == set(STRATEGIES)
    assert all(set(v) == set(R.STATES) for v in RT.COMPAT.values())


def test_validated_compatible_and_no_trade_states():
    reg = _reg("STRONG_UPTREND", {"STRONG_UPTREND": 1.0})
    ok = {s["id"]: s for s in RT.route(reg, validated={"momentum": {"STRONG_UPTREND"}},
                                       model_available=True)["strategies"]}
    assert ok["momentum"]["verdict"] == "COMPATIBLE"
    assert ok["ai_adaptive"]["verdict"] == "REQUIRES_VALIDATION"
    for st in ("UNKNOWN", "ABNORMAL", "EVENT_UNCERTAINTY"):
        out = RT.route(_reg(st, {"STRONG_UPTREND": 1.0}),
                       validated={"momentum": {"STRONG_UPTREND", st}})
        assert out["no_trade"] and {s["verdict"] for s in out["strategies"]} == {"NO_TRADE"}
        assert all(s["reasons"] for s in out["strategies"])
    assert RT.route(None)["regime_state"] == "UNKNOWN"


def test_pipeline_and_api_expose_regime_v2_and_router(client, otp):
    rows = demo.candles("NIFTY", "15m", NOW, 300)
    out = pipeline.run(candles=rows, timeframe="15m", symbol="NIFTY", quality={"status": "OK"},
                       is_demo=True, predict_fn=lambda c, r: None, chain_fn=None)
    assert out["regime_v2"]["version"] == R.V2_VERSION
    assert out["strategy_router"]["regime_state"] == out["regime_v2"]["state"]
    assert out["decision"] == "NO_TRADE" and len(out["pipeline"]) == 12
    fail = pipeline.run(candles=rows, timeframe="15m", symbol="NIFTY",
                        quality={"status": "FAIL"}, is_demo=False,
                        predict_fn=lambda c, r: None, chain_fn=None)
    assert fail["regime_v2"]["state"] == "ABNORMAL"
    assert any("Regime v2 state ABNORMAL" in r for r in fail["abstain_reasons"])
    h = auth_header(register_and_verify(client, otp))
    b = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h).json()
    assert b["regime_v2"]["calibration_status"] == "UNCALIBRATED"
    assert b["strategy_router"]["strategies"] and b["regime"]["label"]
