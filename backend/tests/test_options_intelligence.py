"""Options intelligence (work package I): IV context, Monte Carlo, costs, risks, API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.analysis import context as C
from app.execution import charges as CH
from app.market_data import demo
from app.market_data.provider import DemoProvider
from app.options import intelligence as I
from app.options import simulation as SIM
from app.options import volatility as VOL
from app.options.greeks import black_scholes
from tests.conftest import auth_header, register_and_verify

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)


def _leg(ltp: float, iv: float = 12.0, delta: float | None = 0.5, **kw: object) -> dict:
    return {"ltp": ltp, "bid": ltp - 0.5, "ask": ltp + 0.5, "iv": iv, "delta": delta,
            "gamma": 0.001, "theta": -5.0, "vega": 10.0, "oi": 50_000, "oi_change": 100,
            "volume": 5_000, **kw}


# ------------------------------------------------------------- IV context


def test_iv_rank_needs_history_and_never_fabricates():
    out = VOL.iv_rank([], 14.0)
    assert out["iv_rank"] is None and out["reason"] == VOL.INSUFFICIENT_IV_HISTORY
    out = VOL.iv_rank([10.0] * (VOL.MIN_IV_HISTORY - 1), 14.0)
    assert out["iv_rank"] is None and out["history_points"] == VOL.MIN_IV_HISTORY - 1
    hist = [10 + 10 * k / 99 for k in range(100)]  # 10 … 20
    out = VOL.iv_rank(hist, 15.0)
    assert out["iv_rank"] == pytest.approx(0.5) and out["iv_percentile"] == pytest.approx(0.5)
    assert VOL.iv_rank(hist, None)["reason"] == "current IV unavailable"


def test_skew_and_term_structure():
    rows = [{"strike": 100 + 10 * k, "is_atm": k == 2,
             "call": _leg(5, iv=12 - k, delta=0.9 - 0.2 * k),
             "put": _leg(5, iv=16 - k, delta=-0.1 - 0.2 * (4 - k))} for k in range(5)]
    sk = VOL.skew(rows, k=2)
    assert sk["put_minus_call_iv_k"] == pytest.approx(16 - 8)  # put@100 (16) − call@140 (8)
    assert sk["put_minus_call_iv_25d"] is not None
    assert VOL.atm_iv(rows) == pytest.approx((10 + 14) / 2)
    later = [{**r, "call": {**r["call"], "iv": r["call"]["iv"] + 2},
              "put": {**r["put"], "iv": r["put"]["iv"] + 2}} for r in rows]
    ts = VOL.term_structure([{"expiry": date(2026, 11, 3), "rows": rows},
                             {"expiry": date(2026, 11, 10), "rows": later}])
    assert ts["shape"] == "CONTANGO" and len(ts["points"]) == 2
    assert VOL.term_structure([{"expiry": date(2026, 11, 3), "rows": rows}])["reason"]


# ------------------------------------------------------------- simulation


def _sim(**kw: object) -> dict:
    base = {"spot": 100.0, "strike": 100.0, "call": True, "iv": 0.2, "path_vol": 0.2,
            "years_to_expiry": 10 / 365, "horizon_minutes": 120, "entry_premium": 0.0,
            "target_pct": 30.0, "stop_pct": 20.0, "rate": 0.0, "q": 0.0, "paths": 3000}
    base.update(kw)
    if not base["entry_premium"]:
        base["entry_premium"] = float(SIM.bs_price(base["spot"], base["strike"],
                                                   base["years_to_expiry"], base["iv"],
                                                   base["rate"], base["q"], base["call"]))
    return SIM.simulate(SIM.SimInput(**base))  # type: ignore[arg-type]


def test_vectorised_price_matches_scalar_black_scholes():
    for call in (True, False):
        g = black_scholes(spot=101, strike=100, years=0.05, vol=0.18, rate=0.065, call=call)
        v = SIM.bs_price(101.0, 100.0, 0.05, 0.18, 0.065, 0.0, call)
        assert float(v) == pytest.approx(g.price, rel=1e-9)


def test_simulation_is_seeded_and_probabilities_sum_to_one():
    a, b = _sim(seed=3), _sim(seed=3)
    assert a == b
    c = _sim(seed=4)
    assert c != a
    tot = a["p_target_first"]["p"] + a["p_stop_first"]["p"] + a["p_neither"]["p"]
    assert tot == pytest.approx(1.0, abs=1e-3)


def test_zero_volatility_only_time_decay():
    # No underlying movement: an OTM call can only lose value through theta.
    out = _sim(strike=100.5, path_vol=1e-9, years_to_expiry=1 / 365, horizon_minutes=300,
               stop_pct=5.0)
    assert out["p_target_first"]["p"] == 0.0
    assert out["p_stop_first"]["p"] == 1.0
    assert out["expected_premium_pnl_per_unit"] < 0


def test_deep_itm_call_moves_one_to_one_and_matches_martingale_hitting_probability():
    g = black_scholes(spot=100, strike=50, years=0.5, vol=0.2, rate=0.0, call=True)
    assert g.delta == pytest.approx(1.0, abs=1e-6)
    # Premium ≈ S − K, so premium +3% / −1% ≈ underlying +1.5 / −0.5. A driftless
    # GBM is a martingale: P(up barrier first) = (S0 − L) / (U − L) = 0.5 / 2 = 0.25.
    out = _sim(strike=50.0, years_to_expiry=0.5, target_pct=3.0, stop_pct=1.0,
               horizon_minutes=375 * 10, paths=20000, steps=2000, seed=11)
    hit = out["p_target_first"]["p"] + out["p_stop_first"]["p"]
    assert hit > 0.97
    assert out["p_target_first"]["p"] / hit == pytest.approx(0.25, abs=0.025)


def test_iv_shock_scenarios_move_premium_with_vega():
    up, down = _sim(iv_shock_pts=2.0, path_vol=1e-9), _sim(iv_shock_pts=-2.0, path_vol=1e-9)
    assert up["expected_premium_pnl_per_unit"] > down["expected_premium_pnl_per_unit"]


# ------------------------------------------------------------- contract analysis


def _demo_inputs() -> tuple[dict, C.Ctx, datetime]:
    p = DemoProvider(now=lambda: NOW)
    r = p.option_chain("NIFTY", None, 6)
    ctx = C.build(demo.candles("NIFTY", "15m", NOW, 300), timeframe="15m")
    return r.data, ctx, r.as_of


def test_four_outputs_are_separate_and_labelled():
    chain, ctx, now = _demo_inputs()
    out = I.analyse(chain=chain, source="DEMO", quality_status="OK", side="CE", now=now,
                    ctx=ctx, available_funds=10_000, risk_per_trade=250, paths=1500)
    assert out["data_source"] == "SIMULATED" and out["lot_size"] == 1
    assert out["lot_size_source"] == "DEMO_PLACEHOLDER"
    assert out["iv_context"]["iv_rank"] is None
    assert out["iv_context"]["reason"] == "insufficient IV history"
    assert out["futures_basis"]["value"] is None
    assert len(out["candidates"]) == 5
    assert [c["rank"] for c in out["candidates"]] == [1, 2, 3, 4, 5]
    for c in out["candidates"]:
        assert c["underlying_direction"]["probabilities"] is None
        assert c["underlying_direction"]["reason"]
        pp, en = c["premium_path"], c["expected_net"]
        assert pp["label"] == en["label"] == "MODEL_ESTIMATE"
        assert pp["available"] and pp["path_volatility_source"] == "REALIZED_HV20"
        assert {s["iv_shock_pts"] for s in pp["iv_scenarios"]} == {-2.0, 2.0}
        costs = en["costs"]
        assert costs["total"] == pytest.approx(costs["spread_crossing"] + costs["slippage"]
                                               + costs["charges"], abs=0.011)
        assert en["expected_net_pnl"] == pytest.approx(en["expected_premium_pnl"]
                                                       - costs["total"], abs=0.011)
        assert c["greeks"]["source"] == "MODEL"
        r = c["risks"]
        assert r["theta_per_day_lot"] == pytest.approx(c["greeks"]["theta"], abs=0.01)
        assert set(r["iv_move_pnl_lot"]) == {"-2", "+2"}
        assert c["moneyness"] in ("ATM", "ITM", "OTM")
    assert sum(c["moneyness"] == "ATM" for c in out["candidates"]) == 1
    assert out["assumptions"]["risk_free_rate"] == I.RATE


def test_charges_come_from_the_versioned_schedule():
    chain, ctx, now = _demo_inputs()
    atm = next(r for r in chain["rows"] if r["is_atm"])
    out = I.analyse(chain=chain, source="DEMO", quality_status="OK", side="PE", now=now,
                    strike=atm["strike"], ctx=ctx, paths=500, brokerage=Decimal("20"))
    (c,) = out["candidates"]
    entry = c["ask"] + 0.05
    rt = CH.round_trip(buy=Decimal(str(round(entry, 2))), sell=Decimal(str(round(entry, 2))),
                       qty=1, day=now.date(), brokerage=Decimal("20"))
    # Brokerage dominates on a one-unit lot; charges at a similar exit price must agree.
    assert c["expected_net"]["costs"]["charges"] == pytest.approx(float(rt["total"]["total"]),
                                                                  abs=0.5)


def test_affordability_and_risk_flags_block_contracts():
    chain, ctx, now = _demo_inputs()
    out = I.analyse(chain=chain, source="DEMO", quality_status="OK", side="CE", now=now,
                    ctx=ctx, available_funds=1.0, risk_per_trade=0.5, paths=300)
    for c in out["candidates"]:
        codes = {f["code"] for f in c["risks"]["flags"]}
        assert {"UNAFFORDABLE", "RISK_ABOVE_LIMIT"} <= codes
        assert c["eligible"] is False and c["blocked_reasons"]


def test_liquidity_spread_and_expiry_flags():
    now = NOW
    exp = (now + timedelta(hours=10)).date()
    rows = [{"strike": 100.0, "is_atm": True,
             "call": {**_leg(10.0), "bid": 9.0, "ask": 11.0, "oi": 10, "volume": 1},
             "put": _leg(10.0)}]
    chain = {"underlying": "X", "expiry": exp, "spot": 100.0, "lot_size": 50,
             "tick_size": 0.05, "rows": rows, "greeks_model": "Provided by INDstocks"}
    out = I.analyse(chain=chain, source="LIVE", quality_status="OK", side="CE", now=now,
                    paths=300)
    (c,) = out["candidates"]
    codes = {f["code"] for f in c["risks"]["flags"]}
    assert {"WIDE_SPREAD", "LOW_OI", "LOW_VOLUME", "NEAR_EXPIRY"} <= codes
    assert c["greeks"]["source"] == "PROVIDER" and out["lot_size_source"] == "INSTRUMENT_MASTER"
    assert c["risks"]["spread_cost_lot"] == pytest.approx(2.0 * 50)
    assert c["affordability"]["affordable"] is None  # funds not supplied: not checked


def test_missing_greeks_are_computed_and_labelled_model():
    rows = [{"strike": 100.0, "is_atm": True,
             "call": {**_leg(3.0), "delta": None, "gamma": None, "theta": None, "vega": None},
             "put": None}]
    chain = {"underlying": "X", "expiry": (NOW + timedelta(days=7)).date(), "spot": 100.0,
             "lot_size": 50, "tick_size": 0.05, "rows": rows,
             "greeks_model": "Provided by INDstocks"}
    out = I.analyse(chain=chain, source="LIVE", quality_status="OK", side="CE", now=NOW,
                    paths=300)
    g = out["candidates"][0]["greeks"]
    assert g["source"] == "MODEL" and 0.4 < g["delta"] < 0.6
    put = I.analyse(chain=chain, source="LIVE", quality_status="OK", side="PE", now=NOW)
    assert put["candidates"] == []  # no put contract at this strike: nothing invented


def test_real_chain_without_lot_size_evaluates_nothing():
    chain, _, now = _demo_inputs()
    out = I.analyse(chain={**chain, "lot_size": None}, source="LIVE", quality_status="OK",
                    side="CE", now=now)
    assert out["candidates"] == [] and out["lot_size"] is None
    assert any("Lot size" in n for n in out["notes"])


def test_promoted_direction_is_passed_through_not_estimated():
    chain, ctx, now = _demo_inputs()
    d = {"probabilities": {"DOWN": 0.2, "SIDEWAYS": 0.3, "UP": 0.5}, "model_version": "m1"}
    out = I.analyse(chain=chain, source="DEMO", quality_status="OK", side="PE", now=now,
                    ctx=ctx, direction=d, strikes=0, paths=300)
    ud = out["candidates"][0]["underlying_direction"]
    assert ud["relevant_outcome"] == "DOWN" and ud["relevant_probability"] == 0.2


# ------------------------------------------------------------------- API


def test_intelligence_endpoint(client, otp):
    h = auth_header(register_and_verify(client, otp))
    r = client.get("/api/v1/options/NIFTY/intelligence?side=PE&strikes=1&stop_pct=25"
                   "&target_pct=40&horizon_minutes=45", headers=h)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["is_demo"] and b["data_source"] == "SIMULATED" and b["side"] == "PE"
    assert len(b["candidates"]) == 3
    assert b["iv_context"]["iv_rank"] is None
    assert b["iv_context"]["reason"] == "insufficient IV history"
    assert b["underlying_direction"]["probabilities"] is None
    assert b["funds"]["risk_per_trade"] == 250.0
    assert b["funds"]["available_funds"] == pytest.approx(10_000)
    c = b["candidates"][0]
    assert {"underlying_direction", "premium_path", "expected_net", "risks"} <= set(c)
    assert c["premium_path"]["horizon_minutes"] == 45
    assert len(b["iv_context"]["term_structure"]["points"]) >= 2
    one = client.get(f"/api/v1/options/NIFTY/intelligence?strike={c['strike']}",
                     headers=h).json()
    assert [x["strike"] for x in one["candidates"]] == [c["strike"]]
    assert client.get("/api/v1/options/NIFTY/intelligence?side=XX", headers=h).status_code \
        == 422
    assert client.get("/api/v1/options/NIFTY/intelligence").status_code == 401
