from __future__ import annotations

import functools
import math
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.analysis import context as C
from app.backtesting import cache
from app.backtesting import engine as E
from app.backtesting import metrics as M
from app.backtesting import validation as V
from app.backtesting.data import LookaheadError, PastView, session_bars
from app.backtesting.service import SIMULATED_WARNING, load_bars
from app.core.time import IST
from app.execution import charges as CH
from app.market_data.provider import DemoProvider
from tests.conftest import auth_header, register_and_verify
from tests.test_phase2_api import BOT

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
MON = date(2026, 10, 26)
EXE0 = E.Execution(spread_bps=0, slippage_ticks=0, charge_premium=100.0)
RISK0 = E.Risk(cooldown_minutes=0)
STOP, TARGET = E.Level("POINTS", 1.0), E.Level("POINTS", 2.0)


def day_bars(d: date, n: int = 75, price: float = 100.0) -> list[dict]:
    start = datetime.combine(d, time(9, 15), IST)
    out = []
    for k in range(n):
        c = price + (0.01 if k % 2 else -0.01)
        out.append({"ts": (start + timedelta(minutes=5 * k)).astimezone(UTC), "open": price,
                    "high": price + 0.1, "low": price - 0.1, "close": c, "volume": 1000})
    return out


def flat(days: int = 1, start: date = MON) -> list[dict]:
    out, d = [], start
    while days:
        if d.weekday() < 5:
            out += day_bars(d)
            days -= 1
        d += timedelta(days=1)
    return out


def setbar(candles: list[dict], i: int, o: float, h: float, lo: float, c: float) -> None:
    candles[i].update(open=o, high=h, low=lo, close=c)


def prep_of(candles: list[dict]) -> E.Prepared:
    return E.Prepared(C.build(candles, intraday=True))


def at(*ks: int):
    return lambda v: v.i in ks


def strat(hook, **kw) -> E.Strategy:
    return E.Strategy(hook=hook, stop=STOP, target=TARGET, **kw)


@functools.cache
def _demo(timeframe: str, limit: int) -> tuple[dict, ...]:
    return tuple(session_bars(DemoProvider(now=lambda: NOW).candles("NIFTY", timeframe,
                                                                   limit).data))


def demo_session(timeframe: str = "5m", limit: int = 9000) -> list[dict]:
    return list(_demo(timeframe, limit))


# ------------------------------------------------------------------ metrics


def _t(net: float, gross: float, charges: float) -> dict:
    return {"net": net, "gross": gross, "charges": charges, "entry_price": 100.0,
            "exit_price": 101.0, "qty": 1, "slippage": 0.5, "gap_slippage": 0.0}


def test_metrics_on_hand_built_trade_list():
    trades = [_t(100, 110, 10), _t(-50, -40, 10), _t(30, 40, 10), _t(-20, -10, 10),
              _t(-10, 0, 10), _t(60, 70, 10)]
    m = M.trade_metrics(trades)
    assert m["trades"] == 6 and m["wins"] == 3 and m["losses"] == 3
    assert m["gross_pnl"] == 170 and m["charges_total"] == 60 and m["net_pnl"] == 110
    assert m["cost_contribution_pct"] == 35.29  # 60 / 170
    assert m["win_rate_pct"] == 50.0
    assert m["profit_factor"] == 2.375  # 190 / 80
    assert m["expectancy"] == 18.33
    assert m["avg_winner"] == 63.33 and m["avg_loser"] == -26.67
    assert m["max_consecutive_losses"] == 2
    assert m["turnover"] == 1206.0 and m["slippage_total"] == 3.0
    nets = [100, -50, 30, -20, -10, 60]
    mean = 110 / 6
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / 5)
    assert m["per_trade_sharpe"] == round(mean / sd, 3)

    dd_abs, dd_pct = M.drawdown([10000, 10100, 10050, 10080, 10060, 10050, 10110])
    assert dd_abs == 50 and dd_pct == pytest.approx(50 / 10100 * 100)

    r = M.daily_ratios([10, -5], capital=1000)
    # daily returns 1% and -0.5%: mean 0.25%, sample sd 1.0607%, downside rms 0.35355%
    assert r["sharpe"] == pytest.approx(0.0025 / 0.0106066 * math.sqrt(252), abs=1e-3)
    assert r["sortino"] == pytest.approx(0.0025 / 0.00353553 * math.sqrt(252), abs=1e-3)
    assert M.daily_ratios([5], 1000)["sharpe"] is None

    empty = M.trade_metrics([])
    assert empty["trades"] == 0 and empty["profit_factor"] is None
    assert empty["win_rate_pct"] is None and empty["expectancy"] is None
    no_loss = M.trade_metrics([_t(5, 6, 1)])
    assert no_loss["profit_factor"] is None and no_loss["avg_loser"] is None


def test_group_summary_and_stability():
    trades = [dict(_t(10, 12, 2), regime="RANGE"), dict(_t(-4, -2, 2), regime="RANGE"),
              dict(_t(6, 8, 2), regime="TRENDING_UP")]
    g = M.group_summary(trades, lambda t: t["regime"])
    assert g["RANGE"]["trades"] == 2 and g["RANGE"]["net_pnl"] == 6
    assert g["RANGE"]["profit_factor"] == 2.5 and g["TRENDING_UP"]["win_rate_pct"] == 100
    s = M.stability([{"net_pnl": 10, "win_rate_pct": 50, "expectancy": 5, "profit_factor": 2},
                     {"net_pnl": -10, "win_rate_pct": 30, "expectancy": -5,
                      "profit_factor": 0.5}])
    assert s["net_pnl_mean"] == 0 and s["net_pnl_std"] == pytest.approx(14.142, abs=1e-3)
    assert s["profitable_windows_pct"] == 50


# ------------------------------------------------------------------ fills


def test_stop_assumed_first_when_stop_and_target_in_same_bar():
    cs = flat()
    setbar(cs, 61, 100, 103, 98, 101)  # entry bar: range covers stop 99 and target 102
    raw = E.run(prep_of(cs), strat(at(60)), EXE0, RISK0)
    (t,) = raw["trades"]
    assert t["entry_index"] == 61 and t["entry_price"] == 100  # next-bar open
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == 99
    assert t["stop_target_same_bar"] is True and t["gross"] == -1


def test_target_fills_at_target_when_only_target_is_hit():
    cs = flat()
    setbar(cs, 62, 100, 102.5, 99.5, 102)
    (t,) = E.run(prep_of(cs), strat(at(60)), EXE0, RISK0)["trades"]
    assert t["exit_reason"] == "TARGET" and t["exit_price"] == 102 and t["gross"] == 2


def test_gap_through_stop_fills_at_open_and_records_gap_slippage():
    cs = flat()
    setbar(cs, 62, 97, 97.5, 96.5, 97)
    (t,) = E.run(prep_of(cs), strat(at(60)), EXE0, RISK0)["trades"]
    assert t["exit_reason"] == "STOP_LOSS_GAP" and t["exit_price"] == 97
    assert t["gap_slippage"] == 2.0 and t["gross"] == -3.0


def test_spread_and_slippage_are_adverse_on_both_legs():
    cs = flat()
    exe = E.Execution(spread_bps=20, slippage_ticks=2, tick_size=0.05, charge_premium=100.0)
    (t,) = E.run(prep_of(cs), strat(at(60)), exe, RISK0)["trades"]
    # entry: 100 + half spread 0.10 + 2 ticks 0.10; square-off exit at 100 - 0.20
    assert t["entry_price"] == 100.2 and t["exit_price"] == 99.8
    assert t["exit_reason"] == "SQUARE_OFF" and t["slippage"] == pytest.approx(0.4)


def test_latency_and_worst_case_entry_fill():
    cs = flat()
    setbar(cs, 62, 100, 101, 99.5, 100.5)
    exe = E.Execution(spread_bps=0, slippage_ticks=0, charge_premium=100.0, latency_bars=1,
                      entry_fill="NEXT_BAR_WORST")
    (t,) = E.run(prep_of(cs), strat(at(60)), exe, RISK0)["trades"]
    assert t["entry_index"] == 62 and t["entry_price"] == 101  # bar high for a long


def test_square_off_time_and_no_overnight_positions():
    cs = flat(days=2)
    raw = E.run(prep_of(cs), strat(lambda v: True), EXE0,
                E.Risk(cooldown_minutes=0, max_entries_per_day=20))
    trades = raw["trades"]
    assert trades
    for t in trades:
        entry = datetime.fromisoformat(t["entry_time"])
        exit_ = datetime.fromisoformat(t["exit_time"])
        assert entry.date() == exit_.date()  # nothing carried overnight
        assert datetime.fromisoformat(t["signal_time"]).time() < time(15, 0)
        assert t["exit_reason"] == "SQUARE_OFF" and exit_.time() == time(15, 15)
    assert all(datetime.fromisoformat(t["entry_time"]).time() < time(15, 15) for t in trades)


def test_charges_use_dated_schedule_and_reduce_net():
    for d, sched in ((MON, "nse-opt-2026-04-01"), (date(2026, 3, 2), "nse-opt-2024-10-01")):
        cs = flat(start=d)
        (t,) = E.run(prep_of(cs), strat(at(60)), EXE0, RISK0)["trades"]
        b = CH.compute(side="BUY", price=Decimal("100.0"), qty=1, day=d, brokerage=Decimal("20"))
        s = CH.compute(side="SELL", price=Decimal("100.0"), qty=1, day=d,
                       brokerage=Decimal("20"))
        assert t["charge_schedule"] == sched
        assert t["charges"] == float((b + s).total)
        assert t["net"] == pytest.approx(t["gross"] - t["charges"])
    # STT rose from 0.1% to 0.15% on 1 Apr 2026: later trades cost more.
    new = E.run(prep_of(flat()), strat(at(60)), EXE0, RISK0)["trades"][0]["charges"]
    old = E.run(prep_of(flat(start=date(2026, 3, 2))), strat(at(60)), EXE0,
                RISK0)["trades"][0]["charges"]
    assert new > old


def test_entries_per_day_and_re_entry_rules():
    cs = flat(days=2)
    for k in range(len(cs)):  # every bar's range covers the 1-point stop
        setbar(cs, k, 100, 103, 97, 100)
    p = prep_of(cs)
    raw = E.run(p, strat(lambda v: True), EXE0,
                E.Risk(cooldown_minutes=0, max_entries_per_day=2, re_entry_after_stop_loss=True))
    days = [datetime.fromisoformat(t["entry_time"]).date() for t in raw["trades"]]
    assert len(raw["trades"]) == 4 and days.count(days[0]) == 2
    assert raw["suppressed"]["MAX_ENTRIES_PER_DAY"] > 0
    raw = E.run(p, strat(lambda v: True), EXE0, E.Risk(cooldown_minutes=0))
    assert len(raw["trades"]) == 2  # one per day: no re-entry after a stop-loss
    assert raw["suppressed"]["NO_RE_ENTRY_AFTER_STOP_LOSS"] > 0


def test_cooldown_daily_loss_stop_and_risk_budget():
    cs = flat(days=1)
    for k in range(len(cs)):
        setbar(cs, k, 100, 103, 97, 100)
    p = prep_of(cs)
    raw = E.run(p, strat(lambda v: True), EXE0,
                E.Risk(cooldown_minutes=30, max_entries_per_day=20,
                       re_entry_after_stop_loss=True))
    starts = [datetime.fromisoformat(t["signal_time"]) for t in raw["trades"]]
    assert all((b - a) >= timedelta(minutes=30) for a, b in zip(starts, starts[1:], strict=False))
    assert raw["suppressed"]["COOLDOWN"] > 0
    big = E.Risk(cooldown_minutes=0, max_entries_per_day=20, re_entry_after_stop_loss=True,
                 lot_size=900, risk_per_trade=5000)
    raw = E.run(p, strat(lambda v: True), EXE0, big)
    assert len(raw["trades"]) == 1 and raw["trades"][0]["net"] < -800
    assert raw["suppressed"]["DAILY_LOSS_STOP"] > 0
    raw = E.run(p, strat(lambda v: True), EXE0, E.Risk(lot_size=900))
    assert raw["trades"] == [] and raw["suppressed"]["RISK_BUDGET"] > 0


def test_rejections_missing_liquidity_and_simulated():
    cs = flat()
    cs[61]["volume"] = 0
    raw = E.run(prep_of(cs), strat(at(60)), E.Execution(min_volume=1), RISK0)
    assert raw["trades"] == [] and raw["rejected"] == {"NO_LIQUIDITY": 1}
    raw = E.run(prep_of(flat()), strat(lambda v: True), E.Execution(reject_probability=0.5),
                E.Risk(cooldown_minutes=0, max_entries_per_day=20), seed=3)
    assert raw["rejected"].get("SIMULATED_REJECTION", 0) > 0


def test_partial_fill_halves_lots_when_more_than_one_lot():
    raw = E.run(prep_of(flat()), strat(at(60)), E.Execution(partial_fill_probability=1.0),
                E.Risk(lots=4, cooldown_minutes=0))
    (t,) = raw["trades"]
    assert t["qty"] == 2 and t["partial_fill"] is True


# ------------------------------------------------------------------ determinism / lookahead


def test_same_request_and_seed_give_identical_results():
    sess = demo_session()[-800:]
    s = E.Strategy(conditions=[{"indicator": "RSI", "params": {"period": 14},
                                "operator": "CROSS_ABOVE", "value": 50}])
    exe = E.Execution(reject_probability=0.3)
    a = E.run(prep_of(sess), s, exe, RISK0, seed=11)
    b = E.run(prep_of(sess), s, exe, RISK0, seed=11)
    assert a["trades"] and a["trades"] == b["trades"] and a["rejected"] == b["rejected"]
    assert E.summary(a) == E.summary(b)


def test_strategy_hook_cannot_read_future_bars():
    cs = flat()
    seen = []

    def honest(v):
        seen.append((v.i, len(v.close), v.close[-1]))
        return False

    p = prep_of(cs)
    E.run(p, E.Strategy(hook=honest), EXE0, RISK0)
    assert seen and all(n == i + 1 and last == p.ctx.c[i] for i, n, last in seen)

    def peek(v):
        return v.close[v.i + 1] > v.close[v.i]

    with pytest.raises(LookaheadError):
        E.run(prep_of(cs), E.Strategy(hook=peek), EXE0, RISK0)

    def peek_series(v):
        return v.series("rsi14")[v.i + 1] is not None

    with pytest.raises(LookaheadError):
        E.run(prep_of(cs), E.Strategy(hook=peek_series), EXE0, RISK0)
    pv = PastView([1, 2, 3, 4], 1)
    assert list(pv) == [1, 2] and pv[-1] == 2 and pv[0:10] == [1, 2] and len(pv) == 2


def test_appending_future_bars_does_not_change_earlier_trades():
    sess = demo_session()[-700:]
    days = [b["ts"].astimezone(IST).date() for b in sess]
    k = next(i for i in range(450, len(sess)) if days[i] != days[i - 1])  # a day boundary
    s = E.Strategy(conditions=[
        {"indicator": "RSI", "params": {"period": 14}, "operator": "CROSS_ABOVE", "value": 50},
        {"indicator": "PATTERN", "params": {"name": "BULLISH_ENGULFING"}, "operator": "PRESENT"},
        {"indicator": "RESISTANCE_BREAK", "params": {"lookback": 20},
         "operator": "CLOSE_ABOVE"}], logic="ANY")
    short = E.run(prep_of(sess[:k]), s, EXE0, RISK0)["trades"]
    long = E.run(prep_of(sess), s, EXE0, RISK0)["trades"]
    assert short
    assert short == [t for t in long if t["entry_index"] < k]


# ------------------------------------------------------------------ validation


def test_chronological_split():
    sp = V.chronological_split(1000, 60, 20)
    assert sp == {"train": (0, 600), "validation": (600, 800), "test": (800, 1000)}
    with pytest.raises(ValueError):
        V.chronological_split(100, 80, 20)


def test_walk_forward_aggregates_only_out_of_sample_windows():
    p = prep_of(demo_session()[-1500:])
    base = E.Strategy(conditions=[{"indicator": "RSI", "params": {"period": 14},
                                   "operator": "CROSS_ABOVE", "value": 50}])
    grid = V.Grid((0.2, 0.4), (0.4, 0.8), (45.0, 55.0))
    res = V.walk_forward(p, base, grid, E.Execution(), E.Risk(), seed=1, train_bars=300,
                         test_bars=150, holdout_pct=20, objective="net_pnl", min_trades=1,
                         max_windows=8)
    hold = len(p) - int(len(p) * 0.2)
    ws = res["windows"]
    assert len(ws) >= 3 and res["grid_size"] == 8
    tests = [(w["test"]["start_index"], w["test"]["end_index"]) for w in ws]
    for w in ws:
        assert w["train"]["end_index"] == w["test"]["start_index"]  # train precedes test
        assert w["test"]["end_index"] <= hold
    assert all(a[1] <= b[0] for a, b in zip(tests, tests[1:], strict=False))  # no overlap
    oos = res["oos_raw"]["trades"]
    assert len(oos) == sum(w["test_trades"] for w in ws) == res["oos_metrics"]["trades"]
    assert all(any(a <= t["entry_index"] < b for a, b in tests) for t in oos)
    assert res["oos_metrics"]["net_pnl"] == pytest.approx(
        sum(w["test_metrics"]["net_pnl"] for w in ws), abs=0.05)
    h = res["holdout"]
    assert h["evaluated_once"] is True and h["span"]["start_index"] == hold
    assert h["params_chosen_on"]["end_index"] == hold
    assert res["stability"]["windows"] == len(ws)
    with pytest.raises(ValueError):
        V.walk_forward(p, base, grid, E.Execution(), E.Risk(), seed=1, train_bars=1400,
                       test_bars=150, holdout_pct=20, objective="net_pnl", min_trades=1,
                       max_windows=8)


def test_sensitivity_surface_covers_grid():
    p = prep_of(demo_session()[-600:])
    base = E.Strategy(conditions=[{"indicator": "RSI", "params": {"period": 14},
                                   "operator": "CROSS_ABOVE", "value": 50}])
    res = V.sensitivity(p, base, V.Grid((0.2, 0.3, 0.5), (0.5, 1.0)), E.Execution(), E.Risk(),
                        seed=1)
    assert len(res["cells"]) == 6 and res["in_sample"] is True
    assert {(c["params"]["stop_pct"], c["params"]["target_pct"]) for c in res["cells"]} == {
        (s, t) for s in (0.2, 0.3, 0.5) for t in (0.5, 1.0)}


def test_monte_carlo_percentiles_shape_and_determinism():
    nets = [120.0, -80.0, 45.0, -30.0, 60.0, -55.0, 10.0]
    a = V.monte_carlo(nets, simulations=500, seed=9)
    b = V.monte_carlo(nets, simulations=500, seed=9)
    assert a == b
    for k in ("net_pnl", "max_drawdown"):
        vals = [a[k][f"p{p}"] for p in V.PERCENTILES]
        assert list(a[k]) == ["p5", "p25", "p50", "p75", "p95"] and vals == sorted(vals)
    assert 0 <= a["probability_of_loss"] <= 1 and a["trades_per_simulation"] == 7
    assert a["max_drawdown"]["p5"] >= 0
    assert V.monte_carlo(nets, simulations=500, seed=10) != a
    pos = V.monte_carlo([5.0, 5.0], simulations=100, seed=1)
    assert pos["probability_of_loss"] == 0 and pos["net_pnl"]["p5"] == pos["net_pnl"]["p95"] == 10
    with pytest.raises(ValueError):
        V.monte_carlo([], simulations=100, seed=1)


def test_load_bars_filters_to_session_and_caches_prepared():
    cache.PREPARED.clear()
    prov = DemoProvider(now=lambda: NOW)
    p1, info = load_bars(prov, "NIFTY", "5m", 300)
    p2, _ = load_bars(prov, "NIFTY", "5m", 300)
    assert p1 is p2 and info["bars"] == 300 and info["source"] == "DEMO"
    assert info["dropped_outside_session"] > 0
    for t in p1.ctx.ts:
        lt = t.astimezone(IST)
        assert lt.weekday() < 5 and time(9, 15) <= lt.time() < time(15, 30)


# ------------------------------------------------------------------ API


@pytest.fixture
def h(client, otp):
    cache.RESULTS.clear()
    return auth_header(register_and_verify(client, otp))


RUN = {"strategy_id": "momentum", "underlying": "NIFTY", "timeframe": "5m", "bars": 300}


def test_api_requires_auth(client):
    assert client.get("/api/v1/backtests/strategies").status_code == 401
    for path in ("run", "walk-forward", "sensitivity", "monte-carlo"):
        assert client.post(f"/api/v1/backtests/{path}", json={}).status_code == 401


def test_api_strategies_lists_backtestable(client, h):
    r = client.get("/api/v1/backtests/strategies", headers=h)
    assert r.status_code == 200
    by_id = {s["id"]: s for s in r.json()}
    assert by_id["momentum"]["backtestable"] is True
    assert by_id["momentum"]["params"]["threshold"]["default"] == 55
    assert by_id["ai_adaptive"]["backtestable"] is False and by_id["dca_grid"]["reason"]
    assert by_id["custom"]["requires_entry_conditions"] is True


def test_api_run_is_labelled_demo_proxy(client, h):
    r = client.post("/api/v1/backtests/run", headers=h, json=RUN)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["fidelity"] == "UNDERLYING_PROXY"
    assert b["fidelity_note"] == "incomplete proxy for options: ignores premium, IV, theta, spread"
    assert b["data_source"] == "DEMO" and b["evidence"] is False
    assert b["warnings"][0] == SIMULATED_WARNING
    assert b["data"]["bars"] == 300 and b["risk_limits"]["risk_per_trade"] == 250
    assert b["risk_limits"]["max_entries_per_day"] == 2 and b["risk_limits"]["lots"] == 1
    m = b["metrics"]
    for k in ("gross_pnl", "net_pnl", "charges_total", "cost_contribution_pct", "win_rate_pct",
              "profit_factor", "expectancy", "max_drawdown", "max_drawdown_pct", "sharpe",
              "sortino", "max_consecutive_losses", "exposure_pct", "turnover", "trades"):
        assert k in m
    assert b["by_timeframe"]["5m"]["trades"] == m["trades"]
    assert len(b["equity_curve"]) <= 200 and "by_regime" in b
    r2 = client.post("/api/v1/backtests/run", headers=h, json=RUN)
    same = r2.json()
    if same["data"]["fingerprint"] == b["data"]["fingerprint"]:
        assert same["cached"] is True and same["metrics"] == m


def test_api_options_fidelity_is_422(client, h):
    r = client.post("/api/v1/backtests/run", headers=h,
                    json={**RUN, "fidelity": "HISTORICAL_OPTIONS"})
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "OPTION_HISTORY_UNAVAILABLE"
    assert e["message"] == ("Historical option premiums are not available from the configured "
                            "data source")


@pytest.mark.parametrize("body,code", [
    ({**RUN, "bars": 5000}, "VALIDATION_ERROR"),
    ({**RUN, "bot_id": "7f1e5d5e-58a8-4a2b-8d0e-0e8c1b8f0c11"}, "VALIDATION_ERROR"),
    ({**RUN, "strategy_id": "ai_adaptive"}, "STRATEGY_NOT_BACKTESTABLE"),
    ({**RUN, "strategy_id": "custom"}, "STRATEGY_NOT_BACKTESTABLE"),
    ({**RUN, "underlying": "DOGE"}, "VALIDATION_ERROR"),
    ({**RUN, "stop": {"type": "PERCENT", "value": 50}}, "VALIDATION_ERROR"),
    ({**RUN, "risk": {"risk_per_trade": 1000}}, "RISK_OVERRIDE_EXCEEDS_LIMIT"),
    ({**RUN, "risk": {"max_entries_per_day": 5}}, "RISK_OVERRIDE_EXCEEDS_LIMIT"),
    ({**RUN, "risk": {"lots": 2}}, "RISK_OVERRIDE_EXCEEDS_LIMIT"),
    ({**RUN, "risk": {"square_off_time_ist": "15:25"}}, "RISK_OVERRIDE_EXCEEDS_LIMIT"),
    ({**RUN, "risk": {"re_entry_after_stop_loss": True}}, "RISK_OVERRIDE_EXCEEDS_LIMIT"),
    ({**RUN, "entry_conditions": [{"indicator": "MODEL_PROBABILITY", "operator": "GT",
                                   "value": 0.7}]}, "UNSUPPORTED_CONDITION"),
])
def test_api_validation(client, h, body, code):
    r = client.post("/api/v1/backtests/run", headers=h, json=body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code


def test_api_stricter_overrides_and_custom_conditions(client, h):
    body = {**RUN, "strategy_id": "custom", "risk": {"risk_per_trade": 100,
                                                     "max_entries_per_day": 1},
            "entry_conditions": [{"indicator": "RSI", "params": {"period": 14},
                                  "operator": "CROSS_ABOVE", "value": 50}]}
    r = client.post("/api/v1/backtests/run", headers=h, json=body)
    assert r.status_code == 200, r.text
    b = r.json()
    lim = b["risk_limits"]
    assert lim["risk_per_trade"] == 100 and lim["max_entries_per_day"] == 1
    days = [t["entry_time"][:10] for t in b["trades"]]
    assert len(days) == len(set(days))


def test_api_run_from_owned_bot_only(client, otp, h):
    r = client.post("/api/v1/bots", headers=h, json={"creation_method": "MANUAL", "config": BOT})
    assert r.status_code == 201, r.text
    bot_id = r.json()["id"]
    r = client.post("/api/v1/backtests/run", headers=h, json={"bot_id": bot_id, "bars": 300})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["strategy"]["bot_id"] == bot_id and b["strategy"]["id"] == "breakout"
    assert any("option premium" in w for w in b["warnings"])
    other = auth_header(register_and_verify(client, otp, "other@example.com"))
    r = client.post("/api/v1/backtests/run", headers=other, json={"bot_id": bot_id, "bars": 300})
    assert r.status_code == 404


def test_api_walk_forward_sensitivity_and_monte_carlo(client, h):
    run = {**RUN, "bars": 900}
    r = client.post("/api/v1/backtests/walk-forward", headers=h,
                    json={"run": run, "train_bars": 300, "test_bars": 150, "min_trades": 1})
    assert r.status_code == 200, r.text
    wf = r.json()
    assert wf["data_source"] == "DEMO" and wf["evidence"] is False
    assert wf["windows"] and wf["holdout"]["evaluated_once"] is True and "oos_metrics" in wf
    r = client.post("/api/v1/backtests/walk-forward", headers=h,
                    json={"run": run, "grid": {"stops_pct": [0.1, 0.2, 0.3, 0.4, 0.5],
                                               "targets_pct": [0.2, 0.4, 0.6, 0.8]}})
    assert r.status_code == 422  # grid cap
    r = client.post("/api/v1/backtests/sensitivity", headers=h,
                    json={"run": RUN, "grid": {"stops_pct": [0.2, 0.3],
                                               "targets_pct": [0.5, 1.0],
                                               "thresholds": [50, 60]}})
    assert r.status_code == 200, r.text
    assert len(r.json()["cells"]) == 8
    r = client.post("/api/v1/backtests/monte-carlo", headers=h,
                    json={"run": {**RUN, "strategy_id": "custom", "entry_conditions": [
                        {"indicator": "RSI", "operator": "CROSS_ABOVE", "value": 50}]},
                        "simulations": 200, "seed": 3})
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        mc = r.json()["monte_carlo"]
        assert mc["simulations"] == 200 and set(mc["net_pnl"]) == {"p5", "p25", "p50", "p75",
                                                                   "p95"}
        assert r.json()["data_source"] == "DEMO"
    else:
        assert r.json()["error"]["code"] == "INSUFFICIENT_DATA"
