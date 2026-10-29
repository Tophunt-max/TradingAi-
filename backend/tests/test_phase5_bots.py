from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis import context as C
from app.bots.engine import MAX_CONSECUTIVE_ERRORS, run_once
from app.core.config import get_settings
from app.core.time import utcnow
from app.market_data import demo
from app.market_data.provider import DemoProvider
from app.models import BotEvent, BotRun, BotSignal
from app.strategies.conditions import Evaluator, mirror, supported
from tests.conftest import auth_header, register_and_verify
from tests.test_phase2_api import BOT

NOW = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)  # Wednesday 10:30 IST
ALWAYS = {"logic": "ALL", "conditions": [{"indicator": "RSI", "operator": "GT", "value": 0}]}


def bot_cfg(**over) -> dict:
    cfg = copy.deepcopy(BOT)
    cfg["entry"] = ALWAYS
    for k, v in over.items():
        cfg[k] = v
    return cfg


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


def create(client, h, **over) -> dict:
    r = client.post("/api/v1/bots", headers=h,
                    json={"creation_method": "MANUAL", "config": bot_cfg(**over)})
    assert r.status_code == 201, r.text
    return r.json()


def start(client, h, bot) -> dict:
    r = client.post(f"/api/v1/bots/{bot['id']}/start", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def tick(engine, now, owner="w1", provider=None):
    with Session(engine) as db:
        return run_once(db, get_settings(), owner,
                        provider or (lambda _db: DemoProvider(now=lambda: now)), now)


def signals(engine) -> list[BotSignal]:
    with Session(engine) as db:
        return list(db.scalars(select(BotSignal).order_by(BotSignal.bar_ts)).all())


# --------------------------------------------------------------- conditions


def test_condition_support_and_mirroring():
    assert supported({"indicator": "RSI", "operator": "GT", "value": 55}) is None
    assert "needs a value" in supported({"indicator": "RSI", "operator": "GT"})
    assert "not supported" in supported({"indicator": "MAGIC", "operator": "GT", "value": 1})
    assert "does not support" in supported({"indicator": "PATTERN", "operator": "GT",
                                            "value": 1})
    m = mirror({"indicator": "RSI", "params": {"period": 14}, "operator": "GT", "value": 55})
    assert (m["operator"], m["value"]) == ("LT", 45)
    m = mirror({"indicator": "PATTERN", "params": {"name": "BULLISH_ENGULFING"},
                "operator": "PRESENT"})
    assert m["params"]["name"] == "BEARISH_ENGULFING"
    assert mirror({"indicator": "SMC_CHOCH", "params": {}, "operator": "PRESENT"})[
        "operator"] == "PRESENT"


def test_crossover_conditions_fire_only_on_the_cross_candle():
    rows = demo.candles("NIFTY", "5m", NOW, 300)
    fires = []
    for cut in range(100, 300):
        ctx = C.build(rows[:cut])
        ev = Evaluator(ctx, "CE")
        fires.append(ev.evaluate({"indicator": "EMA_CROSS", "params": {"fast": 9, "slow": 21},
                                  "operator": "CROSS_ABOVE"}))
    assert any(fires) and not all(fires)
    # Never two consecutive True values: a cross is a single-candle event.
    assert not any(a and b for a, b in zip(fires, fires[1:], strict=False))


def test_evaluator_unknown_and_unavailable_return_none():
    ctx = C.build(demo.candles("NIFTY", "5m", NOW, 120))
    ev = Evaluator(ctx, "CE", probability=lambda: None)
    assert ev.evaluate({"indicator": "MODEL_PROBABILITY", "operator": "GT", "value": 0.7}) is None
    assert ev.evaluate({"indicator": "NOPE", "operator": "GT", "value": 1}) is None
    assert ev.evaluate({"indicator": "RSI", "operator": "GT", "value": 0}) is True


# ---------------------------------------------------------------- lifecycle


def test_state_machine_via_api(client, h):
    bot = create(client, h)
    assert client.post(f"/api/v1/bots/{bot['id']}/pause", headers=h).status_code == 409
    b = start(client, h, bot)
    assert b["status"] == "ACTIVE" and b["run"]["status"] == "RUNNING"
    assert b["run"]["mode"] == "PAPER" and b["health"] == "WAITING_FOR_WORKER"
    r = client.post(f"/api/v1/bots/{bot['id']}/start", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_BOT_STATE"
    assert client.put(f"/api/v1/bots/{bot['id']}", headers=h,
                      json={"config": bot_cfg()}).status_code == 409
    assert client.delete(f"/api/v1/bots/{bot['id']}?confirm=true", headers=h).status_code == 409

    b = client.post(f"/api/v1/bots/{bot['id']}/pause", headers=h).json()
    assert b["status"] == "PAUSED" and b["run"]["status"] == "PAUSED" and b["health"] == "PAUSED"
    b = client.post(f"/api/v1/bots/{bot['id']}/resume", headers=h).json()
    assert b["status"] == "ACTIVE" and b["run"]["status"] == "RUNNING"
    first_run = b["run"]["id"]
    b = client.post(f"/api/v1/bots/{bot['id']}/stop", headers=h).json()
    assert b["status"] == "STOPPED" and b["run"]["status"] == "ENDED"
    b = start(client, h, bot)
    assert b["run"]["id"] != first_run
    runs = client.get(f"/api/v1/bots/{bot['id']}/runs", headers=h).json()
    assert [r["status"] for r in runs] == ["RUNNING", "ENDED"]
    kinds = [e["message"].split(":")[0] for e in
             client.get(f"/api/v1/bots/{bot['id']}/events", headers=h).json()]
    assert kinds == ["Start", "Stop", "Resume", "Pause", "Start"]


def test_start_requires_activation_checks(client, h):
    bad = create(client, h, strategy_id="dca_grid", timeframe="15m")
    r = client.post(f"/api/v1/bots/{bad['id']}/start", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    unsupported = create(client, h, entry={"logic": "ALL", "conditions": [
        {"indicator": "MAGIC", "operator": "GT", "value": 1}]})
    rep = client.post(f"/api/v1/bots/{unsupported['id']}/validate", headers=h).json()
    cond = next(c for c in rep["checks"] if c["key"] == "conditions")
    assert cond["status"] == "FAIL" and rep["can_activate"] is False
    ok = client.post(f"/api/v1/bots/{create(client, h)['id']}/validate", headers=h).json()
    assert ok["can_activate"] is True and ok["can_place_orders"] is True
    assert ok["activation_mode"] == "PAPER"
    engine_chk = next(c for c in ok["checks"] if c["key"] == "engine")
    assert engine_chk["required_for"] == "ORDERS"


# ------------------------------------------------------------------- engine


def test_signals_are_idempotent_and_respect_cooldown_and_limits(client, h, engine):
    bot = create(client, h)
    start(client, h, bot)
    assert tick(engine, NOW) == {"SIGNAL": 1}
    assert tick(engine, NOW) == {"NO_NEW_BAR": 1}               # same candle: nothing new
    assert tick(engine, NOW + timedelta(minutes=5)) == {"SUPPRESSED": 1}  # 15-min cooldown
    assert tick(engine, NOW + timedelta(minutes=20)) == {"SIGNAL": 1}
    assert tick(engine, NOW + timedelta(minutes=40)) == {"SUPPRESSED": 1}  # bot limit 2/day
    sigs = signals(engine)
    assert len(sigs) == 2 and all(s.side == "CE" and s.data_source == "DEMO" for s in sigs)
    ev = client.get(f"/api/v1/bots/{bot['id']}/events", headers=h).json()
    msgs = [e["message"] for e in ev if e["kind"] == "SUPPRESSED"]
    assert any("Bot daily limit" in m for m in msgs) and any("Cooldown" in m for m in msgs)
    assert all("no order placed" in e["message"] for e in ev if e["kind"] == "SIGNAL")
    b = client.get(f"/api/v1/bots/{bot['id']}", headers=h).json()
    assert b["last_signal"].startswith("CE at") and b["run"]["signals_count"] == 2


def test_global_daily_limit_is_shared_by_all_bots(client, h, engine):
    client.patch("/api/v1/risk/limits", headers=h, json={"max_entries_per_day": 3})
    a = create(client, h, name="Bot A")
    b = create(client, h, name="Bot B")
    for x in (a, b):
        start(client, h, x)
    assert tick(engine, NOW) == {"SIGNAL": 2}
    out = tick(engine, NOW + timedelta(minutes=20))
    assert out == {"SIGNAL": 1, "SUPPRESSED": 1}
    with Session(engine) as db:
        msgs = db.scalars(select(BotEvent.message).where(BotEvent.kind == "SUPPRESSED")).all()
    assert any("Global daily entry limit reached (3/3)" in m for m in msgs)
    assert len(signals(engine)) == 3


def test_kill_switch_and_session_window_suppress(client, h, engine):
    late = create(client, h, session={"start_ist": "11:00", "end_ist": "15:00",
                                      "cooldown_minutes": 0})
    other = create(client, h)
    start(client, h, late)
    start(client, h, other)
    client.post(f"/api/v1/bots/{other['id']}/pause", headers=h)
    assert tick(engine, NOW) == {"SUPPRESSED": 1}
    client.post(f"/api/v1/bots/{other['id']}/resume", headers=h)
    # Engaging the kill switch blocks new starts and suppresses running bots.
    client.post("/api/v1/risk/kill-switch", headers=h, json={"engaged": True, "confirm": True})
    assert client.post(f"/api/v1/bots/{create(client, h)['id']}/start",
                       headers=h).status_code == 409
    tick(engine, NOW + timedelta(minutes=5))
    assert signals(engine) == []
    with Session(engine) as db:
        msgs = db.scalars(select(BotEvent.message).where(BotEvent.kind == "SUPPRESSED")).all()
    assert any("Outside the bot's session" in m for m in msgs)
    assert any("Kill switch is engaged" in m for m in msgs)
    assert client.get(f"/api/v1/bots/{other['id']}", headers=h).json()["health"] == "BLOCKED"


def test_paused_and_stopped_bots_are_not_evaluated(client, h, engine):
    bot = create(client, h)
    start(client, h, bot)
    client.post(f"/api/v1/bots/{bot['id']}/pause", headers=h)
    assert tick(engine, NOW) == {}
    client.post(f"/api/v1/bots/{bot['id']}/resume", headers=h)
    assert tick(engine, NOW) == {"SIGNAL": 1}
    client.post(f"/api/v1/bots/{bot['id']}/stop", headers=h)
    assert tick(engine, NOW + timedelta(minutes=30)) == {}


def test_leases_prevent_two_workers_and_recover_after_crash(client, h, engine):
    bot = create(client, h)
    start(client, h, bot)
    assert tick(engine, NOW, owner="w1") == {"SIGNAL": 1}
    assert tick(engine, NOW + timedelta(seconds=30), owner="w2") == {"LEASED_ELSEWHERE": 1}
    # w1 "crashes"; after the 60 s lease expires w2 takes over without duplicates.
    after = NOW + timedelta(minutes=20, seconds=61)
    assert tick(engine, after, owner="w2") == {"SIGNAL": 1}
    assert tick(engine, after, owner="w2") == {"NO_NEW_BAR": 1}
    assert len(signals(engine)) == 2


def test_duplicate_signal_for_same_candle_is_rejected_by_db(client, h, engine):
    bot = create(client, h, session={"start_ist": "09:20", "end_ist": "15:00",
                                     "cooldown_minutes": 0})
    start(client, h, bot)
    tick(engine, NOW)
    with Session(engine) as db:
        run = db.scalar(select(BotRun))
        run.last_bar_ts = None  # simulate a crash before the run row was saved
        db.commit()
    assert tick(engine, NOW) == {"DUPLICATE": 1}
    assert len(signals(engine)) == 1


def test_repeated_errors_auto_pause_the_bot(client, h, engine):
    bot = create(client, h)
    start(client, h, bot)

    class Broken:
        def candles(self, *a, **k):
            raise RuntimeError("feed down")

    for i in range(MAX_CONSECUTIVE_ERRORS):
        assert tick(engine, NOW + timedelta(seconds=i), provider=lambda _db: Broken()) == {
            "ERROR": 1}
    b = client.get(f"/api/v1/bots/{bot['id']}", headers=h).json()
    assert b["status"] == "PAUSED" and "consecutive errors" in b["status_reason"]
    assert b["run"]["consecutive_errors"] == MAX_CONSECUTIVE_ERRORS
    with Session(engine) as db:
        errors = db.scalar(select(func.count()).select_from(BotEvent)
                           .where(BotEvent.level == "ERROR"))
    assert errors == 2  # first error and the final one; repeats are not spammed
    alerts = client.get("/api/v1/alerts?category=BOTS", headers=h).json()
    assert any(a["title"].startswith("Bot error") for a in alerts)


def test_run_pins_config_version(client, h, engine):
    bot = create(client, h)
    client.put(f"/api/v1/bots/{bot['id']}", headers=h, json={"config": bot_cfg(name="v2 bot")})
    b = start(client, h, bot)
    assert b["run"]["config_version"] == 2
    tick(engine, NOW)
    assert signals(engine)[0].config_version == 2


def test_health_ok_after_worker_pass_and_alerts(client, h, engine):
    bot = create(client, h, session={"start_ist": "00:00", "end_ist": "23:59",
                                     "cooldown_minutes": 0})
    start(client, h, bot)
    now = utcnow()
    tick(engine, now)
    b = client.get(f"/api/v1/bots/{bot['id']}", headers=h).json()
    assert b["health"] in ("OK", "BLOCKED"), b["health_detail"]
    assert b["health"] == "OK"
    health = client.get("/api/v1/health/ready").json()
    comp = {c["name"]: c for c in health["components"]}
    assert comp["bot_worker"]["status"] == "ok"
    sig_alerts = client.get("/api/v1/alerts?category=SIGNALS", headers=h).json()
    assert sig_alerts and "paper risk manager" in sig_alerts[0]["body"]
    bots_alerts = client.get("/api/v1/alerts?category=BOTS", headers=h).json()
    assert any(a["title"] == "Bot started" for a in bots_alerts)
    listed = client.get("/api/v1/bots?status=ACTIVE", headers=h).json()
    assert listed[0]["id"] == bot["id"] and listed[0]["run"]["last_bar_ts"]


def test_other_users_cannot_control_or_read_bot(client, otp, h):
    bot = create(client, h)
    other = auth_header(register_and_verify(client, otp, "other@example.com"))
    for path in ("start", "pause", "stop"):
        assert client.post(f"/api/v1/bots/{bot['id']}/{path}",
                           headers=other).status_code == 404
    for path in ("signals", "events", "runs"):
        assert client.get(f"/api/v1/bots/{bot['id']}/{path}", headers=other).status_code == 404
    assert client.get(f"/api/v1/bots/{uuid.uuid4()}/events", headers=h).status_code == 404


# ---------------------------------------------------------------- AI draft


def test_ai_draft_auto_side_and_pe_mirroring(client, h):
    r = client.post("/api/v1/bots/ai-draft", headers=h, json={
        "underlying": "NIFTY", "style": "MOMENTUM", "capital": "10000", "option_type": "PE"})
    cfg = r.json()["config"]
    assert cfg["instrument"]["option_type"] == "PE"
    ops = {c["indicator"]: c["operator"] for c in cfg["entry"]["conditions"]}
    assert ops["EMA_CROSS"] == "CROSS_BELOW" and ops["RSI"] == "LT"
    auto = client.post("/api/v1/bots/ai-draft", headers=h, json={
        "underlying": "BANKNIFTY", "style": "AUTO", "capital": "10000",
        "option_type": "AUTO"}).json()
    assert auto["config"]["instrument"]["option_type"] in ("CE", "PE")
    assert any(x.startswith("Side:") for x in auto["rationale"])
    assert any("DEMO" in w for w in auto["warnings"])
    created = client.post("/api/v1/bots", headers=h,
                          json={"creation_method": "AI", "config": auto["config"]}).json()
    rep = client.post(f"/api/v1/bots/{created['id']}/validate", headers=h).json()
    assert rep["can_activate"] is True  # AI drafts are valid within the global limits


def test_strategy_catalog_exposes_pe_defaults(client, h):
    rows = {s["id"]: s for s in client.get("/api/v1/bots/strategies", headers=h).json()}
    pe = {c["indicator"]: c for c in rows["momentum"]["default_entry_pe"]}
    assert pe["EMA_CROSS"]["operator"] == "CROSS_BELOW" and pe["RSI"]["value"] == 45
