"""ta-3 indicators, evidence families, candlestick intelligence and SMC v2."""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime, timedelta

import pytest

from app.analysis import ablation as AB
from app.analysis import context as C
from app.analysis import patterns as PAT
from app.analysis import pipeline
from app.analysis import smc as SMC
from app.indicators import core as ind
from app.indicators import evidence as EV
from app.indicators import structure as ST
from app.market_data import demo
from tests.conftest import auth_header, register_and_verify

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
T0 = datetime(2026, 10, 28, 3, 45, tzinfo=UTC)


def bars(ohlc, volume=1000):
    return [{"ts": T0 + timedelta(minutes=5 * i), "open": o, "high": h, "low": lo, "close": c,
             "volume": volume} for i, (o, h, lo, c) in enumerate(ohlc)]


DECLINE = [(110 - 2 * i, 111 - 2 * i, 107 - 2 * i, 108 - 2 * i) for i in range(20)]  # ends 70
RALLY = [(70 + 2 * i, 73 + 2 * i, 69 + 2 * i, 72 + 2 * i) for i in range(20)]  # ends 110
ROWS = demo.candles("NIFTY", "5m", NOW, 320)
CL = [r["close"] for r in ROWS]
HI = [r["high"] for r in ROWS]
LO = [r["low"] for r in ROWS]
VO = [float(r["volume"]) for r in ROWS]


# ------------------------------------------------- independent reference code


def ref_sma(x, p):
    return [None if i < p - 1 else sum(x[i - p + 1:i + 1]) / p for i in range(len(x))]


def ref_ema(x, p):
    out, prev, a = [], None, 2 / (p + 1)
    for i, v in enumerate(x):
        if i < p - 1:
            out.append(None)
        elif i == p - 1:
            prev = sum(x[:p]) / p
            out.append(prev)
        else:
            prev = a * v + (1 - a) * prev
            out.append(prev)
    return out


def ref_rsi(x, p=14):
    out = [None] * len(x)
    g = [max(x[i] - x[i - 1], 0) for i in range(1, len(x))]
    lo = [max(x[i - 1] - x[i], 0) for i in range(1, len(x))]
    ag, al = sum(g[:p]) / p, sum(lo[:p]) / p
    out[p] = 100 - 100 / (1 + ag / al)
    for i in range(p, len(g)):
        ag = (ag * (p - 1) + g[i]) / p
        al = (al * (p - 1) + lo[i]) / p
        out[i + 1] = 100 - 100 / (1 + ag / al)
    return out


def ref_atr(h, lo, c, p=14):
    tr = [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
                           for i in range(1, len(c))]
    out = [None] * len(c)
    out[p - 1] = sum(tr[:p]) / p
    for i in range(p, len(c)):
        out[i] = (out[i - 1] * (p - 1) + tr[i]) / p
    return out


def close_all(a, b, tol=1e-9):
    assert len(a) == len(b)
    for x, y in zip(a, b, strict=True):
        assert (x is None) == (y is None), (x, y)
        if x is not None:
            assert x == pytest.approx(y, rel=tol, abs=tol)


# ------------------------------------------------------- indicator accuracy


def test_sma_ema_rsi_atr_match_independent_loops():
    close_all(ind.sma(CL, 20), ref_sma(CL, 20))
    for p in (9, 21, 100):
        close_all(ind.ema(CL, p), ref_ema(CL, p))
    close_all(ind.rsi(CL, 14), ref_rsi(CL, 14))
    close_all(ind.atr(HI, LO, CL, 14), ref_atr(HI, LO, CL, 14))


def test_hand_computed_values():
    assert ind.roc([100, 110, 121], 1) == [None, pytest.approx(10), pytest.approx(10)]
    assert ind.momentum([1, 4, 9, 16], 2) == [None, None, 8, 12]
    assert ind.obv([10, 11, 10.5, 10.5, 12], [100, 200, 150, 120, 300]) == [0, 200, 50, 50, 350]
    # Anchored VWAP from bar 1: typical prices 11, 12; volumes 100, 300.
    av = ind.anchored_vwap([12, 13, 14], [10, 11, 12], [11, 12, 13], [50, 100, 300], 1)
    assert av[0] is None and av[1] == pytest.approx(12.0)
    assert av[2] == pytest.approx((12 * 100 + 13 * 300) / 400)
    # Hidden until the anchor is confirmed.
    assert ind.anchored_vwap([12, 13, 14], [10, 11, 12], [11, 12, 13], [50, 100, 300], 0,
                             start=2)[:2] == [None, None]
    ext = ind.fibonacci_extension(200, 100, up_move=True)
    assert ext["1.618"] == pytest.approx(261.8) and ext["1.000"] == pytest.approx(200)
    abc = ind.fibonacci_extension(200, 100, up_move=True, retrace=150)
    assert abc["1.000"] == pytest.approx(250)
    down = ind.fibonacci_extension(200, 100, up_move=False)
    assert down["1.618"] == pytest.approx(38.2)


def test_historical_and_realized_volatility():
    hv = ind.historical_volatility(CL, 20, 252)
    lr = [math.log(CL[i] / CL[i - 1]) for i in range(1, len(CL))]
    assert hv[19] is None and hv[20] is not None
    for i in (20, 150, 319):
        exp = statistics.stdev(lr[i - 20:i]) * math.sqrt(252)
        assert hv[i] == pytest.approx(exp, rel=1e-12)
    rv = ind.realized_volatility(CL, 20)
    assert rv[100] == pytest.approx(math.sqrt(sum(r * r for r in lr[80:100])), rel=1e-12)
    pk = ind.parkinson_volatility(HI, LO, 20, 252)
    exp = math.sqrt(sum(math.log(HI[j] / LO[j]) ** 2 for j in range(81, 101))
                    / (4 * math.log(2) * 20) * 252)
    assert pk[100] == pytest.approx(exp, rel=1e-12)
    assert ind.periods_per_year("15m") == pytest.approx(252 * 25)
    assert ind.periods_per_year(None) == 252


def test_ichimoku_lines_by_brute_force():
    ich = ind.ichimoku(HI, LO)

    def mid(i, p):
        return (max(HI[i - p + 1:i + 1]) + min(LO[i - p + 1:i + 1])) / 2

    assert ich["tenkan"][7] is None and ich["kijun"][24] is None and ich["senkou_b"][50] is None
    for i in (60, 200, 319):
        assert ich["tenkan"][i] == pytest.approx(mid(i, 9))
        assert ich["kijun"][i] == pytest.approx(mid(i, 26))
        assert ich["senkou_a"][i] == pytest.approx((mid(i, 9) + mid(i, 26)) / 2)
        assert ich["senkou_b"][i] == pytest.approx(mid(i, 52))
        # The cloud drawn at i was computed 26 bars earlier: known at i.
        assert ich["span_a"][i] == ich["senkou_a"][i - 26]
        assert ich["span_b"][i] == ich["senkou_b"][i - 26]
    assert "chikou" not in ich


def test_missing_data_never_yields_partial_windows():
    x = [1.0, 2.0, 3.0, None, 5.0, 6.0, 7.0, float("nan"), 9.0]
    assert ind.sma(x, 3) == [None, None, 2.0, None, None, None, 6.0, None, None]
    e = ind.ema(x, 3)
    assert e[2] == 2.0 and e[3] is None and e[5] is None and e[6] == pytest.approx(6.0)
    assert ind.roc(x, 1)[3] is None and ind.roc(x, 1)[4] is None
    assert ind.historical_volatility(x, 2)[4] is None
    o = ind.obv([1, 2, None, 3, 4], [10, 10, 10, 10, 10])
    assert o == [0, 10, None, 0, 10]  # restarts after the gap


def test_volatility_state_flags_contraction_and_expansion():
    quiet = [(100, 100.2, 99.8, 100.0)] * 40
    normal = [(100, 101.5, 98.5, 100 + (1 if i % 2 else -1)) for i in range(120)]
    wild = [(100, 106, 94, 100 + (5 if i % 2 else -5)) for i in range(20)]
    ctx = C.build(bars(normal[:80] + quiet))
    assert ctx.s["vol_state"][-1] == "CONTRACTION"
    ctx = C.build(bars(normal + wild))
    assert ctx.s["vol_state"][-1] == "EXPANSION"


def test_every_context_series_is_causal_for_every_prefix():
    full = C.build(ROWS, timeframe="5m").s
    for k in range(30, len(ROWS) + 1, 7):
        part = C.build(ROWS[:k], timeframe="5m").s
        for key, series in full.items():
            assert part[key] == series[:k], (key, k)
            assert part[key][k - 1] == series[k - 1]


def test_structure_functions_are_causal():
    atr = ind.atr(HI, LO, CL)
    full_div = ST.divergences(HI, LO, ind.rsi(CL))
    for k in range(80, len(ROWS), 23):
        t = k - 1
        part_atr = atr[:k]
        assert ST.trendlines(HI[:k], LO[:k], CL[:k], part_atr) == \
            ST.trendlines(HI, LO, CL, atr, as_of=t)
        assert ST.range_boundaries(HI[:k], LO[:k], CL[:k], part_atr) == \
            ST.range_boundaries(HI, LO, CL, atr, as_of=t)
        assert ST.swing_points(HI[:k], LO[:k]) == ST.swing_points(HI, LO, as_of=t)
        part = ST.divergences(HI[:k], LO[:k], ind.rsi(CL[:k]))
        assert part == [d for d in full_div if d["confirmed_index"] < k]


def test_divergence_uses_confirmed_pivots():
    # Two swing lows: the second lower in price, the oscillator higher.
    lows = [10, 9, 8, 5, 8, 9, 10, 9, 8, 4, 8, 9, 10, 11]
    highs = [x + 1 for x in lows]
    osc = [50] * 3 + [20] + [50] * 5 + [30] + [50] * 4
    d = ST.divergences(highs, lows, osc, "RSI")
    assert len(d) == 1 and d[0]["direction"] == "BULLISH" and d[0]["kind"] == "regular"
    assert d[0]["detection_index"] == 9 and d[0]["confirmed_index"] == 12
    assert ST.divergences(highs[:12], lows[:12], osc[:12]) == []  # not yet confirmed


def test_trendline_channel_and_range_on_constructed_data():
    # Rising channel: zig-zag of slope 0.5 per bar, amplitude 4.
    closes = [100 + 0.5 * i + (4 if (i // 5) % 2 else -4) * ((i % 5) / 4) for i in range(80)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    atr = ind.atr(highs, lows, closes)
    tl = ST.trendlines(highs, lows, closes, atr)
    assert tl["resistance"] and tl["support"]
    assert tl["resistance"]["slope"] > 0 and tl["support"]["slope"] > 0
    assert tl["resistance"]["touches"] >= 3
    assert tl["channel"] is not None and tl["channel"]["direction"] == "UP"
    flat = [100 + (3 if (i // 5) % 2 else -3) for i in range(80)]
    rb = ST.range_boundaries([c + 0.5 for c in flat], [c - 0.5 for c in flat], flat,
                             ind.atr([c + 0.5 for c in flat], [c - 0.5 for c in flat], flat))
    assert rb["upper"] == pytest.approx(103.5) and rb["lower"] == pytest.approx(96.5)
    assert rb["upper_touches"] >= 2 and rb["lower_touches"] >= 2


def test_swing_labels():
    lows = [10, 9, 8, 5, 8, 9, 10, 9, 8, 6, 8, 9, 10, 11]
    pts = ST.swing_points([x + 1 for x in lows], lows)
    lbl = [(p["kind"], p["label"]) for p in pts]
    assert ("LOW", "L") in lbl and ("LOW", "HL") in lbl
    assert all(p["confirmed_index"] == p["index"] + 3 for p in pts)


# ------------------------------------------------------- evidence families


def test_family_votes_count_each_family_once():
    votes = {"ema_stack": 1, "supertrend": 1, "adx_di": 1, "ichimoku": 1, "rsi": 1, "macd": 1,
             "stochastic": 1, "cci": 1, "roc": 1, "obv": -1, "rvol": 0, "vwap": -1, "atr": 0}
    out = EV.family_votes(votes)
    fam = {f["family"]: f for f in out["families"]}
    assert fam["trend"]["vote"] == 1 and fam["momentum"]["vote"] == 1
    assert fam["volume"]["vote"] == -1 and fam["volatility"]["vote"] == 0
    assert out["net_vote"] == 1 and out["max_votes"] == 3  # 9 bullish indicators ≠ 9 votes
    assert fam["volatility"]["directional"] is False


def test_correlation_matrix_flags_redundant_pairs():
    a = [float(i) for i in range(100)]
    series = {"rsi": a, "cci": [2 * x + 1 for x in a], "obv": [math.sin(i) for i in range(100)],
              "atr": [None] * 100}
    corr = EV.correlation_matrix(series, 100)
    pairs = EV.correlated_pairs(corr, 0.8)
    assert [(p["a"], p["b"]) for p in pairs] == [("rsi", "cci")]
    assert pairs[0]["same_family"] is True and pairs[0]["rho"] == pytest.approx(1.0)
    assert corr["matrix"]["atr"]["rsi"] is None


def test_pipeline_exposes_families_correlation_and_version():
    rows = demo.candles("NIFTY", "15m", NOW, 300)
    out = pipeline.run(candles=rows, timeframe="15m", symbol="NIFTY", quality={"status": "OK"},
                       is_demo=True, predict_fn=lambda c, r: None, chain_fn=None)
    t = out["technical"]
    assert out["indicator_version"] == ind.INDICATOR_VERSION == t["indicator_version"]
    assert [f["family"] for f in t["families"]] == ["trend", "momentum", "volume", "volatility"]
    assert abs(t["net_family_vote"]) <= 3 and "correlated_pairs" in t
    assert {"divergences", "trendlines", "range"} <= set(t)
    assert {"ema100", "roc12", "obv", "hv20", "ichimoku", "volatility_state"} <= \
        set(out["indicators"])
    assert out["explanation"][-1] == "Decision: NO TRADE."
    smc = out["smc"]
    assert {"supply_demand", "breakout_retests", "failed_breakouts", "swings",
            "premium_discount", "disclaimer"} <= set(smc)


# ------------------------------------------------------------- candlesticks


def _last_names(rows):
    ctx = C.build(bars(rows))
    return {e["name"] for e in PAT.candlestick_patterns(ctx)
            if e["confirmed_index"] == len(ctx) - 1}


@pytest.mark.parametrize("prefix,tail,name", [
    (RALLY, [(110, 110.9, 106, 110.8)], "Hanging man"),
    (DECLINE, [(70, 70.1, 66, 70.05)], "Dragonfly doji"),
    (RALLY, [(110, 114, 109.98, 110.05)], "Gravestone doji"),
    (DECLINE, [(70, 72.5, 67.5, 70.1)], "Long-legged doji"),
    (DECLINE, [(70, 74.05, 69.95, 74)], "Bullish marubozu"),
    (DECLINE, [(70, 72, 68.6, 70.6)], "Spinning top"),
    (DECLINE, [(69.5, 71.7, 69.3, 71.5)], "Piercing line"),
    (RALLY, [(110.5, 110.7, 108.3, 108.5)], "Dark cloud cover"),
    (DECLINE, [(70.3, 71.3, 70.1, 71.1)], "Bullish harami"),
    (RALLY, [(109.7, 109.9, 108.7, 108.9)], "Bearish harami"),
    (DECLINE, [(69.5, 71, 69.2, 70.8)], "Tweezer bottom"),
    (RALLY, [(110.5, 111.2, 109, 109.3)], "Tweezer top"),
    (DECLINE, [(70, 72.1, 69.9, 72), (71, 74.1, 70.9, 74), (73, 76.1, 72.9, 76)],
     "Three white soldiers"),
    (RALLY, [(110, 110.1, 107.9, 108), (109, 109.1, 105.9, 106), (107, 107.1, 103.9, 104)],
     "Three black crows"),
    (DECLINE, [(70, 70.5, 66, 66.5), (67, 68.7, 66.8, 68.5), (68.5, 71, 68.3, 70.8)],
     "Three inside up"),
    (RALLY, [(110, 113.5, 109.5, 113), (112.5, 112.7, 110.8, 111), (111, 111.2, 108.5, 109.5)],
     "Three inside down"),
    (DECLINE, [(70, 70.5, 68, 68.5), (68.5, 69, 66, 66.5), (66.5, 69.6, 66.2, 69.4)],
     "Bullish three-bar reversal"),
    (RALLY, [(110, 112, 109.5, 111.5), (111.5, 114, 111, 113.5), (113.5, 113.8, 110.5, 110.8)],
     "Bearish three-bar reversal"),
])
def test_new_candlestick_patterns(prefix, tail, name):
    assert name in _last_names(prefix + tail)


HAMMER = (70, 71.1, 66, 71.0)


def _hammer(after):
    ctx = C.build(bars(DECLINE + [HAMMER] + after))
    return next(e for e in PAT.candlestick_patterns(ctx) if e["name"] == "Hammer"), ctx


def test_pattern_context_and_confirmation_lifecycle():
    e, ctx = _hammer([])
    assert e["confirmation"]["status"] == "PENDING" and e["confirmed"] is False
    assert e["detection_index"] == 20 == e["confirmed_index"]
    assert e["invalidation"] == {"level": 66.0, "rule": "A close below the pattern low 66.00."}
    cx = e["context"]
    assert cx["prior_trend"] == "DOWN"
    assert cx["body_range_ratio"] == pytest.approx(1 / 5.1, abs=1e-4)
    assert cx["lower_wick_ratio"] == pytest.approx(4 / 5.1, abs=1e-4)
    assert cx["close_location"] == pytest.approx(5 / 5.1, abs=1e-4)
    assert cx["relative_volume"] == pytest.approx(1.0)
    assert {"rsi14", "roc12", "nearest_support", "resistance_distance_atr", "volatility_state",
            "regime"} <= set(cx)
    e, _ = _hammer([(71, 72, 70.5, 71.8)])
    assert e["confirmation"]["status"] == "CONFIRMED" and e["confirmation"]["index"] == 21
    e, _ = _hammer([(70, 70.2, 65, 65.5)])
    assert e["confirmation"]["status"] == "INVALIDATED"
    e, _ = _hammer([(70.5, 70.9, 70, 70.5)] * 3)
    assert e["confirmation"]["status"] == "EXPIRED"
    assert "profitable" not in str(e).lower()


def test_confirmation_never_changes_once_decided():
    full = {(e["name"], e["index"]): e["confirmation"]
            for e in PAT.candlestick_patterns(C.build(ROWS))}
    for cut in (120, 200, 290):
        for e in PAT.candlestick_patterns(C.build(ROWS[:cut])):
            conf = e["confirmation"]
            f = full[(e["name"], e["index"])]
            if conf["status"] != "PENDING":
                assert conf == f
            elif f["index"] is not None:
                assert f["index"] >= cut


def test_wilson_interval_known_value():
    lo, hi = PAT.wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4) and hi == pytest.approx(0.9433, abs=1e-4)
    assert PAT.wilson_interval(0, 0) is None


def test_outcome_stats_enter_after_confirmation_net_of_cost():
    after = [(71, 72, 70.5, 71.8), (72, 73, 71.5, 72.5), (72.5, 74, 72, 73.5),
             (73.5, 74.5, 73, 74.0)]
    rows = bars(DECLINE + [HAMMER] + after)
    st = PAT.pattern_outcome_stats(rows, horizon=2, cost_bps=10, min_samples=30)
    ham = next(r for r in st["patterns"] if r["pattern"] == "Hammer")
    # Confirmed at bar 21; enter at bar 22 open (72), exit at bar 23 close (73.5).
    exp = 100 * (73.5 / 72 - 1 - 0.001)
    assert ham["n"] == 1 and ham["mean_net_return_pct"] == pytest.approx(exp, abs=1e-4)
    assert ham["insufficient_sample"] is True and ham["hit_rate"] == 1.0
    assert "by_regime" in ham
    assert "OPEN of the bar after the confirmation bar" in st["outcome_definition"]
    assert st["baseline"]["long"]["n"] > 0


def test_outcome_stats_on_demo_report_samples_and_flags():
    rows = demo.candles("NIFTY", "15m", NOW, 1200)
    st = PAT.pattern_outcome_stats(rows, horizon=4, cost_bps=5, min_samples=30)
    assert st["patterns"]
    for r in st["patterns"]:
        assert r["insufficient_sample"] == (r["n"] < 30)
        lo, hi = r["hit_rate_ci95"]
        assert lo <= r["hit_rate"] <= hi
        assert sum(v["n"] for v in r["by_regime"].values()) == r["n"]


# --------------------------------------------------------------------- SMC


SMC_LISTS = ("structure_events", "order_blocks", "fair_value_gaps", "liquidity_sweeps",
             "equal_levels", "displacement", "supply_demand", "breakout_retests",
             "failed_breakouts", "swings")


def test_every_smc_event_has_detection_confirmation_reference_invalidation_quality():
    ctx = C.build(demo.candles("BANKNIFTY", "5m", NOW, 400))
    out = SMC.analyse(ctx, quality="WARN")
    assert "not evidence of institutional" in out["disclaimer"]
    assert out["thresholds"]["fvg_min_atr"] == 0.1
    for key in SMC_LISTS:
        for e in out[key]:
            assert e["detected_index"] <= e["confirmed_index"], key
            assert {"detected_ts", "confirmed_ts", "reference", "data_quality"} <= set(e), key
            assert set(e["invalidation"]) == {"level", "rule"} and e["invalidation"]["rule"]
            assert e["data_quality"] == "WARN"
    assert out["breakout_retests"] and out["failed_breakouts"] and out["supply_demand"]
    for f in out["failed_breakouts"]:
        j, lvl = f["confirmed_index"], f["level"]
        assert (ctx.c[j] < lvl) if f["direction"] == "BEARISH" else (ctx.c[j] > lvl)
        assert j - f["index"] <= SMC.DEFAULT.failed_breakout_window
    for r in out["breakout_retests"]:
        j, lvl = r["confirmed_index"], r["level"]
        assert (ctx.c[j] > lvl) if r["direction"] == "BULLISH" else (ctx.c[j] < lvl)


def test_new_smc_events_never_repaint():
    rows = demo.candles("RELIANCE", "5m", NOW, 400)
    full = SMC.analyse(C.build(rows))
    for cut in (150, 260, 330):
        part = SMC.analyse(C.build(rows[:cut]))
        for key in SMC_LISTS:
            keys = {(e["name"], e["index"], e["confirmed_index"]) for e in part[key]}
            assert keys == {(e["name"], e["index"], e["confirmed_index"]) for e in full[key]
                            if e["confirmed_index"] < cut}, key


def test_premium_discount_is_relative_to_latest_confirmed_swing_range():
    ctx = C.build(demo.candles("NIFTY", "15m", NOW, 300))
    pd_ = SMC.premium_discount(ctx)
    ph, pl = PAT.pivots(ctx)
    hi, lo = ph[-1][1], pl[-1][1]
    assert pd_["range_high"] == hi and pd_["range_low"] == lo
    assert pd_["equilibrium"] == pytest.approx((hi + lo) / 2)
    pos = (ctx.c[-1] - lo) / (hi - lo)
    assert pd_["position"] == pytest.approx(pos, abs=1e-4)
    exp = ("ABOVE_RANGE" if pos > 1 else "BELOW_RANGE" if pos < 0 else "PREMIUM"
           if pos > 0.55 else "DISCOUNT" if pos < 0.45 else "EQUILIBRIUM")
    assert pd_["zone"] == exp


def test_feature_ablation_runs_both_arms():
    rows = demo.candles("NIFTY", "15m", NOW, 900)
    out = AB.feature_ablation(rows, horizon=4, timeframe="15m", is_demo=True)
    base, plus = out["arms"]["baseline"], out["arms"]["baseline_plus_structure"]
    assert base["oos_samples"] == plus["oos_samples"] > 0
    assert len(plus["features"]) > len(base["features"])
    for arm in (base, plus):
        assert arm["log_loss"] > 0 and 0 <= arm["brier"] <= 2
    assert out["evidence_status"] == "NOT_EVIDENCE" and "SIMULATED" in out["note"]
    assert set(out["delta"]) >= {"log_loss", "brier"}


def test_structure_features_are_causal():
    rows = demo.candles("NIFTY", "15m", NOW, 300)
    full = AB.structure_features(C.build(rows))
    part = AB.structure_features(C.build(rows[:220]))
    assert part == full[:220]


# --------------------------------------------------------------------- API


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


def test_pattern_stats_endpoint_labels_demo_as_not_evidence(client, h):
    r = client.get("/api/v1/analysis/NIFTY/patterns/stats?timeframe=15m&horizon=4&history=800",
                   headers=h)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["source"] == "DEMO" and b["evidence_status"] == "NOT_EVIDENCE"
    assert "SIMULATED" in b["evidence_note"] and b["indicator_version"] == "ta-3"
    assert b["horizon_bars"] == 4 and b["outcome_definition"] and b["quality"]["status"]
    assert all({"n", "hit_rate_ci95", "insufficient_sample"} <= set(p) for p in b["patterns"])
    assert client.get("/api/v1/analysis/NIFTY/patterns/stats").status_code == 401
    assert client.get("/api/v1/analysis/NOPE/patterns/stats", headers=h).status_code == 404


def test_analysis_and_chart_expose_ta3(client, h):
    b = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=h).json()
    assert b["indicator_version"] == "ta-3" and b["technical"]["families"]
    p = next((p for p in b["patterns"] if p.get("confirmation")), None)
    assert p is None or {"status", "rule", "bars_ago"} <= set(p["confirmation"])
    r = client.get("/api/v1/charts/NIFTY/candles?timeframe=5m&limit=100"
                   "&indicators=sma20,ema100,roc12,obv,hv20,ichimoku,avwap,sma50,ema34",
                   headers=h)
    assert r.status_code == 200, r.text
    ind_ = r.json()["indicators"]
    for k in ("sma20", "ema100", "roc12", "obv", "hv20", "ichi_tenkan", "ichi_kijun",
              "ichi_span_a", "ichi_span_b", "avwap", "sma50", "ema34"):
        assert len(ind_[k]) == 100, k
    assert ind_["ema100"][0] is not None  # warm-up comes from extra history
    bad = client.get("/api/v1/charts/NIFTY/candles?indicators=sma999,chikou", headers=h)
    assert bad.status_code == 422
