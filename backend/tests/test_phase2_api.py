from __future__ import annotations

import copy

import pytest

from tests.conftest import auth_header, register_and_verify

BOT = {
    "name": "Nifty Breakout",
    "strategy_id": "breakout",
    "timeframe": "5m",
    "instrument": {"underlying": "NIFTY", "option_type": "CE"},
    "entry": {"logic": "ALL", "conditions": [
        {"indicator": "RELATIVE_VOLUME", "operator": "GT", "value": 1.5}]},
    "exit": {"stop_loss": {"type": "PERCENT", "value": "20"},
             "target": {"type": "PERCENT", "value": "35"}},
    "risk": {"capital_allocation": "10000", "risk_per_trade": "250",
             "max_entries_per_day": 2, "max_open_positions": 1},
}


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


def test_market_endpoints_are_labelled_demo(client, h):
    for path in ("/api/v1/market/quotes", "/api/v1/market/movers",
                 "/api/v1/charts/NIFTY/candles", "/api/v1/options/NIFTY/chain",
                 "/api/v1/options/BANKNIFTY/expiries", "/api/v1/analysis/NIFTY"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, (path, r.text)
        body = r.json()
        assert body["source"] == "DEMO" and body["is_demo"] is True, path
        assert body["is_live"] is False and body["as_of"], path


def test_quotes_filter_and_search(client, h):
    eq = client.get("/api/v1/market/quotes?segment=EQ", headers=h).json()["items"]
    assert eq and all(q["segment"] == "EQ" for q in eq)
    some = client.get("/api/v1/market/quotes?symbols=tcs,infy", headers=h).json()["items"]
    assert {q["symbol"] for q in some} == {"TCS", "INFY"}
    hits = client.get("/api/v1/market/search?q=bank", headers=h).json()
    assert hits[0]["symbol"] == "BANKNIFTY"


def test_candles_with_indicators(client, h):
    r = client.get("/api/v1/charts/NIFTY/candles?timeframe=15m&limit=60"
                   "&indicators=ema9,ema21,rsi14,macd", headers=h)
    body = r.json()
    assert len(body["candles"]) == 60
    for k in ("ema9", "ema21", "rsi14", "macd", "macd_signal", "macd_hist"):
        assert len(body["indicators"][k]) == 60
        assert body["indicators"][k][-1] is not None
    assert client.get("/api/v1/charts/NIFTY/candles?indicators=magic",
                      headers=h).status_code == 422
    assert client.get("/api/v1/charts/NOPE/candles", headers=h).status_code == 404


def test_option_chain_contract(client, h):
    exps = client.get("/api/v1/options/NIFTY/expiries", headers=h).json()["expiries"]
    r = client.get(f"/api/v1/options/NIFTY/chain?expiry={exps[1]}&strikes=5", headers=h)
    body = r.json()
    assert body["expiry"] == exps[1] and len(body["rows"]) == 11
    assert body["lot_size"] is None and "instrument master" in body["metadata_note"]
    assert client.get("/api/v1/options/NIFTY/chain?expiry=2020-01-01",
                      headers=h).status_code == 422
    assert client.get("/api/v1/options/RELIANCE/chain", headers=h).status_code == 422


def test_analysis_abstains(client, h):
    body = client.get("/api/v1/analysis/BANKNIFTY?timeframe=1h", headers=h).json()
    assert body["decision"] == "NO_TRADE" and body["probabilities"] is None


def test_bot_crud_versioning_and_ownership(client, otp, h):
    r = client.post("/api/v1/bots", headers=h, json={"creation_method": "MANUAL", "config": BOT})
    assert r.status_code == 201, r.text
    bot = r.json()
    assert bot["status"] == "DRAFT" and bot["mode"] == "PAPER" and bot["current_version"] == 1
    assert bot["today_pnl"] is None  # no fabricated P&L

    cfg = copy.deepcopy(BOT) | {"name": "Nifty Breakout v2"}
    r = client.put(f"/api/v1/bots/{bot['id']}", headers=h, json={"config": cfg})
    assert r.json()["current_version"] == 2 and r.json()["versions"] == [1, 2]
    assert r.json()["name"] == "Nifty Breakout v2"

    clone = client.post(f"/api/v1/bots/{bot['id']}/clone", headers=h).json()
    assert clone["creation_method"] == "CLONE" and clone["name"].endswith("(copy)")
    assert len(client.get("/api/v1/bots", headers=h).json()) == 2
    assert len(client.get("/api/v1/bots?status=DRAFT", headers=h).json()) == 2

    other = auth_header(register_and_verify(client, otp, "other@example.com"))
    assert client.get(f"/api/v1/bots/{bot['id']}", headers=other).status_code == 404

    assert client.delete(f"/api/v1/bots/{bot['id']}", headers=h).status_code == 400
    assert client.delete(f"/api/v1/bots/{bot['id']}?confirm=true", headers=h).status_code == 204
    assert client.get(f"/api/v1/bots/{bot['id']}", headers=h).status_code == 404


@pytest.mark.parametrize(("patch", "fragment"), [
    ({"mode": "LIVE"}, "mode"),
    ({"instrument": {"underlying": "RELIANCE", "option_type": "CE"}}, "NIFTY and BANKNIFTY"),
    ({"instrument": {"underlying": "NIFTY", "option_type": "CE", "side": "SELL"}}, "side"),
    ({"strategy_id": "martingale"}, "Unknown strategy"),
    ({"risk": {**BOT["risk"], "risk_per_trade": "20000"}}, "exceed the capital"),
    ({"exit": {**BOT["exit"], "stop_loss": {"type": "PERCENT", "value": "100"}}}, "below 100"),
])
def test_bot_config_schema_rejects_unsafe_configs(client, h, patch, fragment):
    cfg = copy.deepcopy(BOT) | patch
    r = client.post("/api/v1/bots", headers=h, json={"creation_method": "MANUAL", "config": cfg})
    assert r.status_code == 422
    assert fragment in r.text


def test_validation_uses_global_limits_and_blocks_activation(client, h):
    cfg = copy.deepcopy(BOT)
    cfg["risk"] |= {"max_entries_per_day": 5, "risk_per_trade": "400"}
    bot = client.post("/api/v1/bots", headers=h,
                      json={"creation_method": "MANUAL", "config": cfg}).json()
    report = client.post(f"/api/v1/bots/{bot['id']}/validate", headers=h).json()
    checks = {c["key"]: c["status"] for c in report["checks"]}
    assert report["can_activate"] is False
    assert checks["entries"] == "FAIL" and checks["risk_per_trade"] == "FAIL"
    # DEMO: paper orders use a labelled placeholder contract, so these are warnings.
    assert checks["contract"] == "WARN" and checks["data"] == "WARN" and checks["engine"] == "PASS"

    r = client.post(f"/api/v1/bots/{bot['id']}/start", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "Entries per day" in r.json()["error"]["message"]
    assert client.get(f"/api/v1/bots/{bot['id']}", headers=h).json()["status"] == "DRAFT"


def test_research_only_strategy_cannot_activate(client, h):
    cfg = copy.deepcopy(BOT) | {"strategy_id": "dca_grid", "timeframe": "15m"}
    bot = client.post("/api/v1/bots", headers=h,
                      json={"creation_method": "TEMPLATE", "config": cfg}).json()
    report = client.post(f"/api/v1/bots/{bot['id']}/validate", headers=h).json()
    assert next(c for c in report["checks"] if c["key"] == "strategy")["status"] == "FAIL"


def test_ai_draft_respects_global_risk(client, h):
    r = client.post("/api/v1/bots/ai-draft", headers=h, json={
        "underlying": "BANKNIFTY", "style": "AUTO", "capital": "50000",
        "risk_profile": "AGGRESSIVE"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generator"] == "analysis-rules-v2"
    assert float(body["config"]["risk"]["capital_allocation"]) == 10000
    assert float(body["config"]["risk"]["risk_per_trade"]) <= 250
    assert any("not by a machine-learning model" in w for w in body["warnings"])
    assert body["analysis_regime"]  # AUTO style uses the analysis engine
    r = client.post("/api/v1/bots", headers=h,
                    json={"creation_method": "AI", "config": body["config"]})
    assert r.status_code == 201


def test_strategies_catalog(client, h):
    ids = [s["id"] for s in client.get("/api/v1/bots/strategies", headers=h).json()]
    assert ids[0] == "momentum" and "dca_grid" in ids


def test_risk_defaults_update_and_kill_switch_persist(client, h):
    r = client.get("/api/v1/risk", headers=h).json()
    lim = r["limits"]
    assert (float(lim["capital"]), float(lim["max_risk_per_trade"]),
            float(lim["daily_loss_limit"]), float(lim["daily_profit_lock"])) == (
        10000, 250, 800, 1200)
    assert lim["max_entries_per_day"] == 2 and lim["max_open_positions"] == 1
    assert lim["re_entry_after_stop_loss"] is False
    assert r["engine_active"] is True and r["new_entries_allowed"] is True

    bad = client.patch("/api/v1/risk/limits", headers=h, json={"max_risk_per_trade": "900"})
    assert bad.status_code == 422
    ok = client.patch("/api/v1/risk/limits", headers=h, json={"max_entries_per_day": 3})
    assert ok.json()["max_entries_per_day"] == 3
    assert client.patch("/api/v1/risk/limits", headers=h,
                        json={"square_off_time_ist": "15:45"}).status_code == 422

    assert client.post("/api/v1/risk/kill-switch", headers=h,
                       json={"engaged": True, "confirm": False}).status_code == 400
    r = client.post("/api/v1/risk/kill-switch", headers=h, json={"engaged": True, "confirm": True})
    assert r.json()["limits"]["kill_switch_engaged"] is True
    assert "Kill switch is engaged." in r.json()["block_reasons"]
    assert client.get("/api/v1/risk", headers=h).json()["limits"]["kill_switch_engaged"] is True

    alerts = client.get("/api/v1/alerts?category=RISK", headers=h).json()
    assert [a["title"] for a in alerts][:2] == ["Kill switch engaged", "Risk limits changed"]


def test_portfolio_orders_positions_are_empty_not_fabricated(client, h):
    p = client.get("/api/v1/portfolio", headers=h).json()
    assert p["mode"] == "PAPER" and p["engine_active"] is True
    assert float(p["total_net_pnl"]) == 0 and float(p["available_funds"]) == 10000
    assert client.get("/api/v1/orders?status=PENDING", headers=h).json()["total"] == 0
    assert client.get("/api/v1/positions", headers=h).json()["items"] == []


def test_brokers_never_claim_live_support(client, h):
    rows = client.get("/api/v1/brokers", headers=h).json()
    assert rows[0]["id"] == "paper" and rows[0]["status"] == "CONNECTED"
    assert all(not b["supports_live_orders"] for b in rows)
    by_id = {b["id"]: b for b in rows}
    # INDstocks has an adapter (unverified against the live API, disabled by default);
    # every other broker is listed as not integrated.
    assert by_id["indstocks"]["status"] == "NOT_ENABLED"
    assert by_id["indstocks"]["verification"] == "UNVERIFIED_AGAINST_LIVE_API"
    assert all(b["status"] == "NOT_INTEGRATED" for b in rows if b["id"] not in
               ("paper", "indstocks"))


def test_phase2_endpoints_require_auth(client):
    for path in ("/api/v1/bots", "/api/v1/risk", "/api/v1/portfolio", "/api/v1/alerts",
                 "/api/v1/brokers", "/api/v1/analysis/NIFTY", "/api/v1/options/NIFTY/chain"):
        assert client.get(path).status_code == 401, path
