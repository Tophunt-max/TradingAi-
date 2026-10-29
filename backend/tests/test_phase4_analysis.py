from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from app.ai import features as F
from app.ai import model as M
from app.analysis import context as C
from app.analysis import patterns as PAT
from app.analysis import pipeline
from app.analysis import regime as R
from app.analysis import smc as SMC
from app.analysis.options_select import shortlist
from app.indicators import core as ind
from app.market_data import demo
from app.strategies.rules import evaluate

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
T0 = datetime(2026, 10, 28, 3, 45, tzinfo=UTC)  # 09:15 IST


def bars(ohlc: list[tuple[float, float, float, float]], minutes: int = 5) -> list[dict]:
    return [{"ts": T0 + timedelta(minutes=minutes * i), "open": o, "high": h, "low": lo,
             "close": c, "volume": 1000} for i, (o, h, lo, c) in enumerate(ohlc)]


def trend_bars(n: int, step: float, start: float = 100.0, noise: float = 0.2,
               seed: int = 1, pct: bool = False) -> list[dict]:
    rnd = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        o = p
        c = (p * (1 + step) if pct else p + step) + rnd.uniform(-noise, noise)
        out.append((o, max(o, c) + 0.3, min(o, c) - 0.3, c))
        p = c
    return bars(out)


# ---------------------------------------------------------------- indicators


def test_vwap_resets_each_session_and_rvol_uses_previous_bars():
    hs, ls, cs, vs = [11, 12, 21], [9, 10, 19], [10, 11, 20], [100, 300, 50]
    v = ind.vwap(hs, ls, cs, vs, ["d1", "d1", "d2"])
    assert v[0] == pytest.approx(10.0)
    assert v[1] == pytest.approx((10 * 100 + 11 * 300) / 400)
    assert v[2] == pytest.approx(20.0)  # new session
    rv = ind.relative_volume([10] * 20 + [30], 20)
    assert rv[19] is None and rv[20] == pytest.approx(3.0)


def test_pivots_fibonacci_and_anatomy():
    p = ind.classic_pivots(110, 90, 100)
    assert p["P"] == pytest.approx(100) and p["R1"] == pytest.approx(110)
    assert p["S1"] == pytest.approx(90) and p["R2"] == pytest.approx(120)
    fib = ind.fibonacci(200, 100, up_move=True)
    assert fib["0.500"] == pytest.approx(150) and fib["0.618"] == pytest.approx(138.2)
    a = ind.candle_anatomy(10, 12, 9, 11)
    assert a["direction"] == "BULLISH" and a["upper_wick"] == 1 and a["lower_wick"] == 1


def test_trend_indicators_agree_on_a_clean_uptrend():
    ctx = C.build(trend_bars(120, 1.0))
    assert ctx.s["adx14"][-1] > 40
    assert ctx.s["pdi14"][-1] > ctx.s["mdi14"][-1]
    assert ctx.s["supertrend_dir"][-1] == 1
    assert ctx.s["supertrend"][-1] < ctx.c[-1]
    assert ctx.s["stoch_k"][-1] > 80 and 0 <= min(v for v in ctx.s["stoch_k"] if v) <= 100
    assert ctx.s["cci20"][-1] > 0


@pytest.mark.parametrize("key", ["vwap", "supertrend", "supertrend_dir", "adx14", "pdi14",
                                 "cci20", "stoch_k", "stoch_d", "rvol20", "atr14", "ema200"])
def test_new_indicators_have_no_lookahead(key):
    rows = demo.candles("BANKNIFTY", "5m", NOW, 300)
    full = C.build(rows).s[key]
    for cut in (120, 200, 299):
        assert C.build(rows[:cut]).s[key] == full[:cut]


# ------------------------------------------------------------------ patterns


def names(events):
    return {e["name"] for e in events}


def test_candlestick_patterns_on_handcrafted_candles():
    decline = [(110 - 2 * i, 111 - 2 * i, 107 - 2 * i, 108 - 2 * i) for i in range(20)]
    last = decline[-1][3]
    hammer = (last, last + 1.1, last - 4, last + 1.0)
    engulf = [(last, last + 0.5, last - 2, last - 1.5), (last - 2, last + 2, last - 2.5, last + 1)]
    ctx = C.build(bars(decline + [hammer]))
    assert "Hammer" in names(PAT.candlestick_patterns(ctx)[-1:])
    ctx = C.build(bars(decline + engulf))
    ev = PAT.candlestick_patterns(ctx)
    assert any(e["name"] == "Bullish engulfing" and e["confirmed_index"] == len(ctx) - 1
               for e in ev)
    star = [(100, 100.5, 89.5, 90), (89.8, 90.3, 89.2, 89.9), (90, 97, 89.8, 96.5)]
    ev = PAT.candlestick_patterns(C.build(bars(decline + star)))
    assert "Morning star" in names(ev)
    inside = [(100, 105, 95, 101), (101, 103, 97, 102)]
    assert "Inside bar" in names(PAT.candlestick_patterns(C.build(bars(decline + inside))))


def test_double_top_needs_neckline_break_to_be_reported():
    up = [(100 + i, 101 + i, 99.5 + i, 100.8 + i) for i in range(20)]  # rally to ~120
    top1 = [(120, 125, 119.5, 121), (121, 121.5, 117, 118), (118, 118.5, 115, 116),
            (116, 116.5, 113, 114), (114, 115, 112.5, 114.5)]
    top2 = [(114.5, 118, 114, 117.5), (117.5, 125.1, 117, 121), (121, 121.5, 118, 118.5),
            (118.5, 119, 116, 116.5), (116.5, 117, 114.5, 115)]
    no_break = top2 + [(115, 115.5, 113.5, 114)] * 2
    breakdown = no_break + [(114, 114.2, 108, 109)]
    ctx = C.build(bars(up + top1 + no_break))
    assert "Double top" not in names(PAT.chart_patterns(ctx))
    ctx = C.build(bars(up + top1 + breakdown))
    ev = [e for e in PAT.chart_patterns(ctx) if e["name"] == "Double top"]
    assert ev and ev[0]["confirmed_index"] == len(ctx) - 1 and ev[0]["direction"] == "BEARISH"


def _keys(events):
    return {(e["name"], e["index"], e["confirmed_index"]) for e in events}


@pytest.mark.parametrize("symbol", ["NIFTY", "RELIANCE", "TATAMOTORS"])
def test_patterns_and_smc_never_repaint(symbol):
    rows = demo.candles(symbol, "5m", NOW, 400)
    full = C.build(rows)
    full_pat = PAT.all_patterns(full)
    full_smc = SMC.analyse(full)
    for cut in (150, 260, 330):
        ctx = C.build(rows[:cut])
        pre = _keys(PAT.all_patterns(ctx))
        assert pre == {k for k in _keys(full_pat) if k[2] < cut}
        part = SMC.analyse(ctx)
        for key in ("structure_events", "fair_value_gaps", "order_blocks", "liquidity_sweeps",
                    "equal_levels", "displacement"):
            assert _keys(part[key]) == {k for k in _keys(full_smc[key]) if k[2] < cut}, key


def test_smc_bos_choch_fvg_and_sweep_on_constructed_path():
    path = ([(100 + i, 101 + i, 99.5 + i, 100.8 + i) for i in range(10)]     # up
            + [(110 - i, 110.5 - i, 108.5 - i, 109 - i) for i in range(8)]   # pullback
            + [(102 + 2 * i, 104 + 2 * i, 101.5 + 2 * i, 103.8 + 2 * i) for i in range(8)]
            + [(118, 118.5, 106, 107), (107, 107.5, 95, 96)]                 # crash
            + [(96, 97, 94, 95)] * 4)
    ctx = C.build(bars(path))
    out = SMC.analyse(ctx)
    ev = [(e["name"], e["direction"]) for e in out["structure_events"]]
    assert ("BOS", "BULLISH") in ev
    assert ("CHOCH", "BEARISH") in ev
    assert any(z["direction"] == "BEARISH" for z in out["fair_value_gaps"])
    assert out["displacement"]
    # Every structure break references a swing confirmed BEFORE the break.
    ph, pl = ind.confirmed_pivots(ctx.h, ctx.l, 3, 3)
    known = {round(p, 6): i + 3 for i, p in ph + pl}
    for e in out["structure_events"]:
        assert known[round(e["level"], 6)] <= e["confirmed_index"]


def test_regime_detection():
    up = R.detect(C.build(trend_bars(200, 1.0)))
    assert up["label"] == "TRENDING_UP" and up["family"] == "TRENDING"
    # Proportional candles keep ATR% stable, like a real steady decline.
    rnd, p, rows = random.Random(4), 400.0, []
    for _ in range(200):
        c = p * (1 - 0.004 + rnd.uniform(-0.0005, 0.0005))
        rows.append((p, max(p, c) * 1.0008, min(p, c) * 0.9992, c))
        p = c
    down = R.detect(C.build(bars(rows)))
    assert down["label"] == "TRENDING_DOWN"
    flat = R.detect(C.build(trend_bars(200, 0.0, noise=1.0)))
    assert flat["label"] in ("RANGE", "SQUEEZE", "RANGE_EXPANSION", "VOLATILE")
    assert flat["family"] != "TRENDING"


# ---------------------------------------------------------------- strategies


def test_strategy_router_shapes_and_ai_adaptive_gate():
    ctx = C.build(demo.candles("NIFTY", "15m", NOW, 300))
    reg = R.detect(ctx)
    pats, smc = PAT.all_patterns(ctx), SMC.analyse(ctx)
    out = {s["id"]: s for s in evaluate(ctx, "15m", reg, pats, smc, None)}
    assert out["dca_grid"]["direction"] == "SKIPPED"
    assert out["ai_adaptive"]["direction"] == "UNAVAILABLE"
    for sid in ("momentum", "scalping", "breakout", "reversal", "smc"):
        assert out[sid]["direction"] in ("CE", "PE", "NONE") and out[sid]["conditions"]
    assert all(c["met"] is False for c in out["scalping"]["conditions"][:1])  # 15m ≠ 1m/3m
    pred = {"probabilities": {"UP": 0.8, "SIDEWAYS": 0.1, "DOWN": 0.1}}
    ai = {s["id"]: s for s in evaluate(ctx, "15m", reg, pats, smc, pred)}["ai_adaptive"]
    assert ai["direction"] == "CE"


def test_strategy_signal_in_wrong_regime_is_flagged():
    ctx = C.build(trend_bars(200, 1.0))
    reg = R.detect(ctx)
    out = {s["id"]: s for s in evaluate(ctx, "5m", reg, [], SMC.analyse(ctx), None)}
    assert out["reversal"]["regime_compatible"] is False  # reversal is a RANGE strategy
    assert out["momentum"]["regime_compatible"] is True


# ---------------------------------------------------------------------- model


def test_purged_walk_forward_never_trains_on_overlapping_labels():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(1000, 4))
    y = rng.integers(0, 3, size=1000)
    wf = M.walk_forward(X, y, horizon=7)
    starts = sorted({wf["test_idx"][0]} | {i for i, j in zip(wf["test_idx"][1:],
                                                           wf["test_idx"][:-1], strict=True)
                                           if i != j + 1})
    for f in wf["folds"]:
        assert f["purge_rows"] == 7
    first_test = wf["test_idx"][0]
    assert wf["folds"][0]["train_rows"] == first_test - 7
    assert len(starts) == 1  # test folds are contiguous


def test_noise_cannot_pass_the_gate():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(1500, 6))
    y = rng.choice(3, size=1500, p=[0.35, 0.3, 0.35])
    metrics = M.evaluate(M.walk_forward(X, y, horizon=4))
    g = M.gate(metrics, "LIVE")
    checks = {c["key"]: c["passed"] for c in g["checks"]}
    assert g["passed"] is False and checks["log_loss"] is False


def test_learnable_signal_passes_but_demo_source_is_blocked():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(2000, 3))
    logits = np.stack([-2 * X[:, 0], np.zeros(2000), 2 * X[:, 0]], axis=1)
    p = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    y = np.array([rng.choice(3, p=row) for row in p])
    metrics = M.evaluate(M.walk_forward(X, y, horizon=2))
    assert M.gate(metrics, "LIVE")["passed"] is True
    demo_gate = M.gate(metrics, "DEMO")
    assert demo_gate["passed"] is False
    assert [c["key"] for c in demo_gate["checks"] if not c["passed"]] == ["data"]


def test_json_params_reproduce_sklearn_probabilities():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(500, 5))
    y = rng.integers(0, 3, size=500)
    params = M.fit(X, y)
    clf = LogisticRegression(C=M.HYPERPARAMS["C"], max_iter=M.HYPERPARAMS["max_iter"],
                             random_state=0)
    mean, scale = np.array(params.mean), np.array(params.scale)
    clf.fit((X - mean) / scale, y)
    ours = M.predict_proba(M.Params.from_dict(params.as_dict()), X[:20])
    np.testing.assert_allclose(ours, clf.predict_proba((X[:20] - mean) / scale), atol=1e-9)
    assert np.allclose(ours.sum(axis=1), 1)


def test_calibration_metrics():
    y = np.array([0, 1, 2, 2])
    perfect = np.eye(3)[y]
    assert M.log_loss(perfect, y) < 1e-6 and M.brier(perfect, y) == pytest.approx(0)
    uniform = np.full((4, 3), 1 / 3)
    assert M.log_loss(uniform, y) == pytest.approx(math.log(3))
    ece, curve = M.calibration(perfect, y)
    assert ece == pytest.approx(0) and curve


def test_features_are_causal_and_labels_need_future_bars():
    rows = demo.candles("NIFTY", "15m", NOW, 300)
    full = C.build(rows)
    part = C.build(rows[:250])
    assert F.row(part, 249) == F.row(full, 249)
    assert F.label(part, 249, 4) is None and F.label(full, 249, 4) is not None
    assert len(F.row(full, 299)) == len(F.FEATURE_NAMES)


# ------------------------------------------------------------------ pipeline


def test_pipeline_always_returns_no_trade_with_twelve_steps():
    rows = demo.candles("NIFTY", "15m", NOW, 300)
    out = pipeline.run(candles=rows, timeframe="15m", symbol="NIFTY", quality={"status": "OK"},
                       is_demo=True, predict_fn=lambda c, r: None, chain_fn=None)
    assert out["decision"] == "NO_TRADE"
    assert [s["key"] for s in out["pipeline"]] == [
        "data", "features", "regime", "technical", "patterns", "strategies", "prediction",
        "contract", "costs", "risk", "decision", "explanation"]
    assert {s["status"] for s in out["pipeline"] if s["key"] in ("costs", "risk")} == {"BLOCKED"}
    assert any("DEMO" in r for r in out["abstain_reasons"])
    assert out["explanation"][-1] == "Decision: NO TRADE."
    assert out["probabilities"] is None


def test_pipeline_quality_fail_is_reported():
    rows = demo.candles("NIFTY", "15m", NOW, 300)
    out = pipeline.run(candles=rows, timeframe="15m", symbol="NIFTY",
                       quality={"status": "FAIL"}, is_demo=False,
                       predict_fn=lambda c, r: None, chain_fn=None)
    assert out["pipeline"][0]["status"] == "FAILED"
    assert "Market-data quality check failed for this window." in out["abstain_reasons"]


def test_option_shortlist_checks_liquidity_and_lot_size():
    leg = {"ltp": 100.0, "bid": 99.5, "ask": 100.5, "oi": 10, "iv": 12.0, "delta": 0.5,
           "theta": -8.0, "trading_symbol": "X"}
    wide = {**leg, "bid": 90.0, "ask": 110.0}
    chain = {"underlying": "NIFTY", "expiry": "2026-11-03", "lot_size": 75, "rows": [
        {"strike": 24400, "is_atm": False, "call": leg, "put": leg},
        {"strike": 24450, "is_atm": True, "call": leg, "put": leg},
        {"strike": 24500, "is_atm": False, "call": wide, "put": leg}]}
    out = shortlist(chain, "CE")
    atm, otm = out["candidates"]
    assert atm["eligible"] and atm["premium_per_lot"] == pytest.approx(100.5 * 75)
    assert atm["theta_per_lot_per_day"] == pytest.approx(-600)
    assert otm["strike"] == 24500 and not otm["eligible"]
    no_lot = shortlist({**chain, "lot_size": None}, "PE")
    assert all(not c["eligible"] for c in no_lot["candidates"])
    assert no_lot["candidates"][1]["strike"] == 24400  # PE OTM is the lower strike
