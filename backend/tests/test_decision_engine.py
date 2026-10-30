"""Multi-factor decision engine (work package L)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import numpy as np
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import core as ACC
from app.api.v1.portfolio import ManualEntry
from app.core.config import get_settings
from app.core.time import utcnow
from app.decision import engine as ENG
from app.decision import evidence as EV
from app.decision import sizing as SZ
from app.decision import weights as W
from app.execution import risk as RISK
from app.indicators.evidence import family_votes
from app.models import DailyRiskState, Order, PaperBrokerOrder
from app.models.bots import RiskSettings
from app.models.intelligence import DecisionRecord
from app.models.user import UserPreferences
from tests.test_phase6_execution import fake, h  # noqa: F401

API = "/api/v1"
NOW = utcnow()


# ------------------------------------------------------------------ helpers


def _item(family, vote, source="technical"):
    return {"source": source, "family": family, "statement": f"{family} {vote}",
            "value": None, "timestamp": NOW, "vote": vote}


def _collected(votes, prediction=None):
    _, _, model = EV.model_items(prediction, NOW)
    return {"votes": votes, "items": [_item(f, v) for f, v in votes.items() if v is not None],
            "ignored": [], "last_structure": None, "model": model,
            "groups": [{f} for f in EV.FAMILIES]}


BULL = {"trend": 1, "momentum": 1, "volume": 1, "candles": 1, "structure": None,
        "model": None}


def _candidate(expected_net=25.0, eligible=True):
    return {"strike": 24000.0, "side": "CE", "trading_symbol": "NIFTY-24000-CE",
            "moneyness": "ATM", "bid": 9.95, "ask": 10.0, "mid": 9.975, "iv": 12.0,
            "oi": 5000, "volume": 500, "eligible": eligible,
            "blocked_reasons": [] if eligible else ["Bid or ask missing."],
            "underlying_direction": {"probabilities": None},
            "premium_path": {"available": True, "p_target_first": {"p": 0.5},
                             "p_stop_first": {"p": 0.4}},
            "expected_net": {"available": True, "expected_net_pnl": expected_net,
                             "costs": {"total": 40.0}, "label": "MODEL_ESTIMATE"},
            "risks": {"flags": []}}


def _sizing(**over):
    kw = dict(entry_premium=D("10"), stop_pct=D("20"), target_pct=D("30"), lot_size=75,
              tick=D("0.05"), max_risk_per_trade=D("250"), max_lots=1, brokerage=D("20"),
              day=date(2026, 10, 29))
    return SZ.propose(**(kw | over))


def _decide(votes=None, *, quality="OK", regime_state="STRONG_UPTREND", no_trade=False,
            event_action="NONE", candidate=None, risk=None, execution=None, prediction=None,
            side=None, sizing=None):
    votes = BULL if votes is None else votes
    collected = _collected(votes, prediction)
    combined = EV.combine(votes, side=side)
    cand = _candidate() if candidate is None else candidate
    return ENG.decide(
        symbol="NIFTY", timeframe="15m", account_mode="DEMO", is_demo=False,
        data_source="LIVE", quality={"status": quality, "issues": []},
        view={"regime_v2": {"state": regime_state, "no_trade": no_trade,
                            "confidence": {regime_state: 0.6},
                            "calibration_status": "UNCALIBRATED", "reasons": []},
              "smc": {"premium_discount": None}, "technical": {}, "last_ts": NOW},
        collected=collected, combined=combined,
        event_risk={"level": {"NO_TRADE": "HIGH"}.get(event_action, "NONE"),
                    "action": event_action, "reasons": [], "as_of": NOW},
        fundamentals={"status": "INDEX", "message": "Index"},
        options={"expiry": date(2026, 11, 5), "lot_size": 75,
                 "lot_size_source": "INSTRUMENT_MASTER", "assumptions": {}},
        candidate=cand, sizing=_sizing() if sizing is None else sizing,
        risk={"allowed": True, "reasons": []} if risk is None else risk,
        setup_levels={"stop_loss": 23900.0, "stop_basis": "swing", "targets": [24100.0],
                      "entry_zone": [23990.0, 24010.0], "risk_reward": [1.0, 2.0]},
        execution=execution or {"available": True, "reason": None},
        weights_info={"used": EV.WEIGHTS_LABEL}, requested_side=side or "auto", now=NOW)


# ------------------------------------------------------------- evidence rule


def test_correlated_indicators_in_one_family_cast_one_vote():
    fam = {f["family"]: f["vote"] for f in family_votes({"rsi": 1, "macd": 1})["families"]}
    assert fam["momentum"] == 1
    out = EV.combine({"momentum": fam["momentum"], "trend": None, "volume": None,
                      "candles": None, "structure": None, "model": None})
    assert out["available_groups"] == 1 and out["bullish"] == 1
    assert out["passed"] is False  # one family is not enough evidence


def test_cross_family_correlated_pair_merges_families():
    groups = EV.correlated_groups([{"a": "ema_stack", "b": "macd", "rho": 0.9,
                                    "same_family": False, "families": ["trend", "momentum"]},
                                   {"a": "rsi", "b": "macd", "rho": 0.95, "same_family": True,
                                    "families": ["momentum", "momentum"]}])
    merged = next(g for g in groups if "trend" in g)
    assert merged == {"trend", "momentum"}
    out = EV.combine({"trend": 1, "momentum": 1, "volume": 1, "candles": None,
                      "structure": None, "model": None}, groups)
    assert out["available_groups"] == 2 and out["bullish"] == 2  # not 3
    assert out["passed"] is False and out["weights"] == "UNWEIGHTED_UNVALIDATED"


def test_majority_of_available_rule():
    ok = EV.combine({"trend": 1, "momentum": 1, "volume": 0, "candles": None,
                     "structure": None, "model": None})
    assert ok["passed"] and ok["side"] == "CE"
    split = EV.combine({"trend": 1, "momentum": -1, "volume": 0, "candles": None,
                        "structure": None, "model": None})
    assert not split["passed"] and split["conflicting"]
    forced = EV.combine({"trend": 1, "momentum": 1, "volume": 1, "candles": None,
                         "structure": None, "model": None}, side="PE")
    assert not forced["passed"] and forced["support"] == 0


def test_unconfirmed_patterns_are_ignored():
    n = 100
    pats = [
        {"name": "Hammer", "kind": "candle", "direction": "BULLISH", "confirmed_index": 98,
         "ts": NOW, "confirmation": {"status": "PENDING", "index": None, "direction": "BULLISH"}},
        {"name": "Bearish engulfing", "kind": "candle", "direction": "BEARISH",
         "confirmed_index": 97, "ts": NOW,
         "confirmation": {"status": "INVALIDATED", "index": 98, "direction": "BEARISH"}},
    ]
    vote, used, ignored = EV.pattern_items(pats, n, None)
    assert vote is None and used == []
    assert len(ignored) == 2 and all("not confirmed" in i["statement"] for i in ignored)
    pats.append({"name": "Bullish engulfing", "kind": "candle", "direction": "BULLISH",
                 "confirmed_index": 96, "ts": NOW, "confirmed_ts": NOW,
                 "confirmation": {"status": "CONFIRMED", "index": 97, "ts": NOW,
                                  "direction": "BULLISH", "rule": "close above"}})
    vote, used, _ = EV.pattern_items(pats, n, None)
    assert vote == 1 and len(used) == 1


def test_structure_uses_latest_confirmed_event():
    smc = {"structure_events": [
        {"name": "BOS", "direction": "BULLISH", "confirmed_index": 90, "level": 1.0},
        {"name": "CHOCH", "direction": "BEARISH", "confirmed_index": 97, "level": 2.0,
         "invalidation": {"level": 3.0, "rule": "close above 3"}}],
        "liquidity_sweeps": [], "failed_breakouts": []}
    vote, items, last = EV.structure_items(smc, 100)
    assert vote == -1 and last["name"] == "CHOCH" and len(items) == 2


# ---------------------------------------------------------------- decisions


def test_all_requirements_give_a_trade_proposal_that_is_not_executed():
    r = _decide()
    assert r["decision"] == "TRADE_PROPOSAL", r["no_trade_reasons"] + r["watch_reasons"]
    req = r["proposal"]["paper_order_request"]
    assert r["proposal"]["requires_user_confirmation"] is True
    assert req["option_type"] == "CE" and req["lots"] == 1
    ManualEntry(**req, idempotency_key="abcdef123456")  # valid for POST /orders/paper
    assert r["setup"]["classification"] == "TREND_CONTINUATION"
    assert r["stop_loss"]["underlying_level"] == 23900.0
    assert r["stop_loss"]["premium_pct"] == "20"
    assert r["evidence"]["supporting"]
    assert r["evidence"]["conflicting"] == []


@pytest.mark.parametrize("kw,needle", [
    ({"quality": "FAIL"}, "quality check failed"),
    ({"regime_state": "ABNORMAL", "no_trade": True}, "ABNORMAL"),
    ({"regime_state": "UNKNOWN", "no_trade": True}, "UNKNOWN"),
    ({"event_action": "NO_TRADE"}, "HIGH-importance"),
    ({"candidate": _candidate(expected_net=0.0)}, "≤ 0"),
    ({"candidate": _candidate(expected_net=-47.5)}, "≤ 0"),
    ({"candidate": _candidate(eligible=False)}, "not eligible"),
    ({"risk": {"allowed": False, "reasons": ["Kill switch is engaged."]}}, "Kill switch"),
    ({"execution": {"available": False, "reason": "REAL account: execution unavailable"}},
     "REAL account"),
])
def test_blocking_items_give_no_trade(kw, needle):
    r = _decide(**kw)
    assert r["decision"] == "NO_TRADE"
    assert r["proposal"] is None
    assert any(needle in x for x in r["no_trade_reasons"]), r["no_trade_reasons"]


def test_missing_non_blocking_item_gives_watch():
    r = _decide({"trend": 1, "momentum": 0, "volume": 0, "candles": None, "structure": None,
                 "model": None})
    assert r["decision"] == "WATCH" and r["no_trade_reasons"] == []
    assert any("Evidence rule not met" in w for w in r["watch_reasons"])


def test_no_probability_is_invented_without_a_promoted_model():
    r = _decide()
    v = r["directional_view"]
    assert v["probabilities"] is None and v["probability_source"] is None
    assert "No promoted" in v["probability_reason"]


def test_model_probability_alone_never_authorises():
    pred = {"probabilities": {"UP": 0.95, "DOWN": 0.03, "SIDEWAYS": 0.02},
            "model_version": str(uuid.uuid4()), "model_name": "m"}
    votes = {"trend": 0, "momentum": -1, "volume": 0, "candles": None, "structure": None,
             "model": 1}
    r = _decide(votes, prediction=pred)
    assert r["decision"] != "TRADE_PROPOSAL"
    assert r["directional_view"]["probabilities"]["UP"] == 0.95
    # Even with evidence that passes, a high probability cannot override other gates.
    r2 = _decide({**BULL, "model": 1}, prediction=pred, regime_state="UNKNOWN", no_trade=True)
    assert r2["decision"] == "NO_TRADE"


# ------------------------------------------------------------------ sizing


def test_sizing_respects_risk_budget_and_max_lots():
    one = _sizing()
    per_lot = D(one["risk_per_lot"])
    assert per_lot == (D("10") - D(one["stop_premium"])) * 75 + D(one["round_trip_charges"])
    big = _sizing(max_risk_per_trade=D("100000"), max_lots=3)
    assert big["lots"] == 3 and big["lots_by_budget"] > 3
    capped = _sizing(max_risk_per_trade=D("100000"), max_lots=50)
    assert capped["lots"] == SZ.MANUAL_MAX_LOTS
    small = _sizing(max_risk_per_trade=D("50"))
    assert small["lots"] == 0 and "above the" in small["notes"][0]
    assert _sizing(max_risk_per_trade=D("100000"), max_lots=5,
                   reduce_exposure=True)["lots"] == 1
    r = _decide(sizing=small)
    assert r["decision"] == "NO_TRADE" and any("0 lots" in x for x in r["no_trade_reasons"])


# -------------------------------------------------------------- risk dry run


def _uid(client, h):  # noqa: F811
    return uuid.UUID(client.get(f"{API}/users/me", headers=h).json()["id"])


def _dry(db, acc, rs):
    return RISK.pretrade_dry_run(
        db, get_settings(), rs, account=acc, now=utcnow(), lots=1, lot_size=75,
        entry_price=D("10"), stop_price=D("8"), round_trip_charges=D("50"),
        buy_charges=D("20"), quote_ok=True, quote_detail="", expiry=date.today()
        + timedelta(days=3), account_blockers=[])


def test_risk_dry_run_blocks_without_creating_anything(client, h, engine):  # noqa: F811
    uid = _uid(client, h)
    with Session(engine) as db:
        acc = ACC.get_or_create_demo(db, uid)
        rs = db.get(RiskSettings, uid) or RiskSettings(user_id=uid)
        db.add(rs)
        db.commit()
        assert _dry(db, acc, rs).allowed
        rs.kill_switch_engaged = True
        d = _dry(db, acc, rs)
        assert not d.allowed and "Kill switch is engaged." in d.reasons
        rs.kill_switch_engaged = False
        db.add(DailyRiskState(user_id=uid, account_id=acc.id, account_mode="DEMO",
                              trading_day=RISK.trading_day(utcnow()), realized_gross=D("-900"),
                              charges=D("0"), entries=0, stop_loss_exits=0))
        db.commit()
        d = _dry(db, acc, rs)
        assert not d.allowed and any("Daily loss stop" in x for x in d.reasons)
        db.rollback()
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder)) == 0
        assert db.scalar(select(func.count()).select_from(DailyRiskState)) == 1  # ours only


def test_dry_run_creates_no_daily_state_row(client, h, engine):  # noqa: F811
    uid = _uid(client, h)
    with Session(engine) as db:
        acc = ACC.get_or_create_demo(db, uid)
        rs = db.get(RiskSettings, uid) or RiskSettings(user_id=uid)
        db.add(rs)
        db.commit()
        assert _dry(db, acc, rs).allowed
        db.commit()
        assert db.scalar(select(func.count()).select_from(DailyRiskState)) == 0


# ---------------------------------------------------------------- weights


def test_learned_weights_not_used_on_demo_or_failed_validation():
    W.clear()
    rng = np.random.default_rng(1)
    X = rng.integers(-1, 2, size=(600, len(W.TRAIN_FAMILIES))).astype(float)
    y = rng.integers(0, 2, size=600)
    demo = W.fit_evidence_weights(X, y, horizon=4, data_source="DEMO")
    assert demo["usable"] is False and any("DEMO" in r for r in demo["reasons"])
    assert demo["metrics"]["oos_samples"] > 0 and set(demo["weights"]) == set(W.TRAIN_FAMILIES)
    noise = W.fit_evidence_weights(X, y, horizon=4, data_source="LIVE")
    assert noise["usable"] is False  # random labels cannot beat the unweighted rule
    W.store("NIFTY", "15m", noise)
    assert W.active("NIFTY", "15m", "LIVE")[0] is None
    W.store("NIFTY", "15m", {**noise, "usable": True})
    assert W.active("NIFTY", "15m", "DEMO")[0] is None  # live data DEMO: never used
    assert W.active("NIFTY", "15m", "LIVE")[0] is not None
    W.clear()
    assert W.active("NIFTY", "15m", "LIVE") == (None, "No learned weights have been fitted "
                                                      "in this process.")


def test_vote_matrix_and_fit_on_demo_candles(client):
    from app.analysis import context as C
    from app.market_data.provider import DemoProvider

    r = DemoProvider().candles("NIFTY", "15m", 400)
    ctx = C.build(r.data, intraday=True, timeframe="15m")
    X, y, net = W.vote_matrix(ctx, 4)
    assert X.shape == (len(y), len(W.TRAIN_FAMILIES)) and len(net) == len(y)
    assert set(np.unique(X)) <= {-1.0, 0.0, 1.0}
    out = W.fit_evidence_weights(X, y, horizon=4, data_source=r.source)
    assert out["usable"] is False


# --------------------------------------------------------------------- API


def test_decision_api_requires_auth(client):
    assert client.get(f"{API}/decision/NIFTY").status_code == 401
    assert client.get(f"{API}/intelligence/dashboard").status_code == 401


def test_decision_api_labels_records_once_and_never_orders(client, h, engine):  # noqa: F811
    r = client.get(f"{API}/decision/NIFTY?timeframe=15m", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["account_mode"] == "DEMO" and d["label"] == "SIMULATED" and d["is_demo"]
    assert d["decision"] in ("NO_TRADE", "WATCH")  # DEMO costs exceed the modelled edge
    assert d["directional_view"]["probabilities"] is None
    assert d["evidence"]["combination"]["weights"] == "UNWEIGHTED_UNVALIDATED"
    assert d["recorded"] == "NEW" and d["decision_record_id"]
    d2 = client.get(f"{API}/decision/NIFTY?timeframe=15m", headers=h).json()
    assert d2["recorded"] == "ALREADY_RECORDED_FOR_BAR"
    assert d2["decision_record_id"] == d["decision_record_id"]
    with Session(engine) as db:
        recs = db.scalars(select(DecisionRecord).where(
            DecisionRecord.origin == "DECISION")).all()
        assert len(recs) == 1 and recs[0].decision == d["decision"]
        assert recs[0].account_mode == "DEMO"
        assert db.scalar(select(func.count()).select_from(Order)) == 0
    hist = client.get(f"{API}/learning/decisions?decision={d['decision']}", headers=h).json()
    assert any(x["origin"] == "DECISION" for x in hist["items"])


def test_forced_side_runs_options_sizing_and_risk_dry_run(client, h, fake, engine):  # noqa: F811
    uid = _uid(client, h)
    client.get(f"{API}/risk", headers=h)  # creates the risk settings row
    with Session(engine) as db:
        db.get(RiskSettings, uid).kill_switch_engaged = True
        db.commit()
    d = client.get(f"{API}/decision/NIFTY?timeframe=15m&side=CE", headers=h).json()
    assert d["side"] == "CE" and d["requested_side"] == "CE"
    assert d["options"]["contract"]["strike"] in (23900.0, 24000.0, 24100.0)
    assert d["sizing"]["lot_size"] == 75
    assert d["risk_check"]["dry_run"] is True and d["risk_check"]["allowed"] is False
    assert any("Kill switch" in x for x in d["no_trade_reasons"])
    assert d["decision"] == "NO_TRADE" and d["proposal"] is None
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder)) == 0
        assert db.scalar(select(func.count()).select_from(DailyRiskState)) == 0


def test_real_mode_says_execution_unavailable(client, h, engine):  # noqa: F811
    uid = _uid(client, h)
    with Session(engine) as db:
        prefs = db.get(UserPreferences, uid)
        prefs.active_account_mode = "REAL"
        db.commit()
    d = client.get(f"{API}/decision/BANKNIFTY?side=PE", headers=h).json()
    assert d["account_mode"] == "REAL"
    assert d["execution"]["available"] is False and d["decision"] == "NO_TRADE"
    assert any("REAL account" in x for x in d["no_trade_reasons"])
    assert d["proposal"] is None


def test_weights_endpoints(client, h, engine):  # noqa: F811
    W.clear()
    s = client.get(f"{API}/decision/weights/status", headers=h).json()
    assert s["default"] == "UNWEIGHTED_UNVALIDATED" and s["latest_fit"] is None
    assert client.post(f"{API}/decision/admin/weights/fit", headers=h).status_code == 403
    W.clear()
