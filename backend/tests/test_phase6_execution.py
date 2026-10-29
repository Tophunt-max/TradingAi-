from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bots.engine import execution_pass, run_once
from app.brokers import paper as paper_mod
from app.brokers.base import BrokerUnavailable
from app.core.config import get_settings
from app.core.time import IST, utcnow
from app.execution import charges as CH
from app.execution import service as EX
from app.execution.state import IllegalTransition, move
from app.market_data.factory import get_provider
from app.market_data.provider import DemoProvider, Result
from app.models import DailyRiskState, Order, PaperBrokerOrder, Position
from tests.conftest import auth_header, register_and_verify
from tests.test_phase5_bots import ALWAYS, bot_cfg

STRIKES = (23900, 24000, 24100)


class FakeOptions:
    """Controllable option chain with LIVE-shaped data and a 75-unit lot."""

    name = "FAKE"

    def __init__(self, source: str = "LIVE", lot_size: int | None = 75) -> None:
        self.source, self.lot_size = source, lot_size
        self.expiry = utcnow().astimezone(IST).date() + timedelta(days=3)
        self.quotes: dict[tuple[str, int], tuple[float | None, float | None]] = {
            (side, k): (9.95, 10.0) for side in ("CE", "PE") for k in STRIKES}
        self.quality = "OK"
        self._demo = DemoProvider()

    def set(self, side: str, strike: int, bid: float | None, ask: float | None) -> None:
        self.quotes[(side, strike)] = (bid, ask)

    def expiries(self, underlying: str) -> Result:
        return Result(data=[self.expiry, self.expiry + timedelta(days=7)], as_of=utcnow(),
                      source=self.source, provider=self.name)

    def option_chain(self, underlying, expiry, strikes) -> Result:  # type: ignore[no-untyped-def]
        def leg(side, k):  # type: ignore[no-untyped-def]
            bid, ask = self.quotes[(side, k)]
            ltp = ask if ask is not None else bid
            return {"security_id": f"{side}{k}", "trading_symbol": f"{underlying}-{k}-{side}",
                    "ltp": ltp, "bid": bid, "ask": ask, "iv": 12.0, "delta": 0.5,
                    "gamma": 0.001, "theta": -5.0, "vega": 10.0, "oi": 1000,
                    "oi_change": 0, "volume": 100}
        rows = [{"strike": float(k), "is_atm": k == 24000, "call": leg("CE", k),
                 "put": leg("PE", k)} for k in STRIKES]
        r = Result(data={"underlying": underlying, "underlying_name": underlying,
                         "expiry": expiry or self.expiry, "spot": 24010.0, "atm_strike": 24000.0,
                         "strike_step": 100.0, "lot_size": self.lot_size,
                         "tick_size": 0.05 if self.lot_size else None, "freeze_qty": 1800,
                         "pcr": 1.0, "greeks_model": "fake", "rows": rows},
                   as_of=utcnow(), source=self.source, provider=self.name)
        if self.quality == "FAIL":
            r.quality.add("STALE_QUOTE", "FAIL", "stale")
        return r

    def candles(self, symbol, timeframe, limit):  # type: ignore[no-untyped-def]
        return self._demo.candles(symbol, timeframe, limit)

    def quotes(self, *a, **k):  # type: ignore[no-untyped-def]
        return self._demo.quotes(*a, **k)

    def search(self, q):  # type: ignore[no-untyped-def]
        return []

    def status(self):  # type: ignore[no-untyped-def]
        return {"provider": self.name, "status": "ok"}


@pytest.fixture
def fake(client):
    f = FakeOptions()
    client.app.dependency_overrides[get_provider] = lambda: f
    yield f
    client.app.dependency_overrides.pop(get_provider, None)


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


def buy(client, h, key=None, **over):
    body = {"underlying": "NIFTY", "option_type": "CE", "lots": 1, "stop_loss_pct": 20,
            "target_pct": 30, "idempotency_key": key or uuid.uuid4().hex[:16]} | over
    r = client.post("/api/v1/orders/paper", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def pass_(engine, fake, now=None, owner="x1"):
    with Session(engine) as db:
        return execution_pass(db, get_settings(), owner, lambda _db: fake, now or utcnow())


def positions(client, h, state="OPEN"):
    return client.get(f"/api/v1/positions?state={state}", headers=h).json()["items"]


# -------------------------------------------------------------------- charges


def test_charges_match_published_rates_and_schedule_dates():
    c = CH.compute(side="BUY", price=D("100"), qty=75, day=date(2026, 10, 28),
                   brokerage=D("20"))
    assert (c.brokerage, c.stt, c.exchange_txn, c.sebi, c.stamp_duty, c.gst) == (
        D("20.00"), D("0.00"), D("2.63"), D("0.01"), D("0.23"), D("4.07"))
    s = CH.compute(side="SELL", price=D("100"), qty=75, day=date(2026, 10, 28),
                   brokerage=D("20"))
    assert s.stt == D("11.25") and s.stamp_duty == D("0.00")  # 0.15% from 1 Apr 2026
    old = CH.compute(side="SELL", price=D("100"), qty=75, day=date(2026, 3, 31),
                     brokerage=D("20"))
    assert old.stt == D("7.50") and old.schedule_id == "nse-opt-2024-10-01"
    with pytest.raises(ValueError):
        CH.schedule_for(date(2020, 1, 1))
    rt = CH.round_trip(buy=D("100"), sell=D("110"), qty=75, day=date(2026, 10, 28),
                       brokerage=D("20"))
    assert D(rt["gross_pnl"]) == D("750.00")
    assert D(rt["net_pnl"]) == D("750.00") - D(rt["total"]["total"])
    be = D(rt["break_even_price"])
    check = CH.round_trip(buy=D("100"), sell=be, qty=75, day=date(2026, 10, 28),
                          brokerage=D("20"))
    assert abs(D(check["net_pnl"])) < D("0.10")


def test_charges_estimate_endpoint(client, h):
    r = client.post("/api/v1/charges/estimate", headers=h,
                    json={"buy_price": "100", "sell_price": "90", "quantity": 75})
    b = r.json()
    assert D(b["gross_pnl"]) == D("-750.00") and D(b["net_pnl"]) < D("-750")
    assert b["schedule_detail"]["stt_sell_pct"] == "0.15"


# ---------------------------------------------------------------- order flow


def test_manual_entry_fills_through_the_state_machine(client, h, fake):
    o = buy(client, h)
    assert o["status"] == "FILLED", o
    assert [e["to_status"] for e in o["events"]] == [
        "CREATED", "VALIDATED", "SUBMITTED", "ACKNOWLEDGED", "FILLED"]
    assert D(o["avg_fill_price"]) == D("10.05")  # ask 10.00 + 1 tick slippage
    assert o["quantity"] == "75" and o["lot_size_source"] == "INSTRUMENT_MASTER"
    assert all(c["passed"] for c in o["risk_checks"])
    assert len(o["fills"]) == 1 and D(o["fills"][0]["charges"]["brokerage"]) == D("20.00")
    p = positions(client, h)[0]
    assert p["status"] == "OPEN" and D(p["stop_loss"]) == D("8.00")  # 20% below the 10.00 ask
    assert D(p["target"]) == D("13.00")
    port = client.get("/api/v1/portfolio", headers=h).json()
    # Premium paid and the buy-side charges both reduce available funds.
    assert D(port["available_funds"]) == D("10000") - D("10.05") * 75 - D(port["charges_today"])
    assert port["trades_today"] == 1 and D(port["charges_today"]) > 0


def test_idempotency_key_never_creates_two_orders(client, h, fake, engine):
    a = buy(client, h, key="same-key-123")
    b = buy(client, h, key="same-key-123")
    assert a["id"] == b["id"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder)) == 1


def test_global_limits_reject_with_reasons(client, h, fake):
    buy(client, h)
    second = buy(client, h)
    assert second["status"] == "REJECTED" and "maximum 1" in second["reject_reason"]
    lots = buy(client, h, lots=3)
    assert "lot(s); allowed 1–1" in lots["reject_reason"]
    fake.set("CE", 24000, 99.95, 100.0)
    client.patch("/api/v1/risk/limits", headers=h, json={"max_open_positions": 3})
    risky = buy(client, h)
    assert risky["status"] == "REJECTED" and "Planned risk" in risky["reject_reason"]


def test_entry_rejected_without_lot_size_or_fresh_quote(client, h, fake):
    fake.quality = "FAIL"
    o = buy(client, h)
    assert o["status"] == "REJECTED" and "fresh two-sided quote" in o["reject_reason"]
    fake.quality, fake.lot_size = "OK", None
    r = client.post("/api/v1/orders/paper", headers=h, json={
        "underlying": "NIFTY", "option_type": "CE", "idempotency_key": "nolot-12345"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_LOT_SIZE"


def test_demo_data_uses_labelled_placeholder_lot(client, h, fake):
    fake.source, fake.lot_size = "DEMO", None
    o = buy(client, h)
    assert o["status"] == "FILLED" and o["lot_size"] == 1
    assert o["lot_size_source"] == "DEMO_PLACEHOLDER" and o["data_source"] == "DEMO"


# --------------------------------------------------------------------- exits


def test_stop_loss_target_and_trailing(client, h, fake, engine):
    buy(client, h, trailing_pct=10)
    fake.set("CE", 24000, 12.0, 12.05)
    pass_(engine, fake)
    p = positions(client, h)[0]
    assert D(p["stop_loss"]) == D("10.80")  # trailed to 10% below the 12.00 high
    fake.set("CE", 24000, 10.5, 10.55)
    pass_(engine, fake)
    assert positions(client, h) == []
    closed = positions(client, h, "CLOSED")[0]
    assert closed["exit_reason"] == "STOP_LOSS"
    assert D(closed["avg_exit"]) == D("10.45")  # bid 10.50 - 1 tick
    assert D(closed["realized_gross"]) == (D("10.45") - D("10.05")) * 75
    assert D(closed["realized_net"]) == D(closed["realized_gross"]) - D(closed["charges"])

    client.patch("/api/v1/risk/limits", headers=h, json={"max_entries_per_day": 3})
    fake.set("CE", 24000, 9.95, 10.0)
    buy(client, h)
    fake.set("CE", 24000, 13.5, 13.55)
    pass_(engine, fake)
    assert positions(client, h, "CLOSED")[0]["exit_reason"] == "TARGET"


def test_daily_loss_stop_persists_and_blocks(client, h, fake, engine):
    client.patch("/api/v1/risk/limits", headers=h,
                 json={"max_risk_per_trade": "200", "daily_loss_limit": "200"})
    buy(client, h)
    fake.set("CE", 24000, 7.0, 7.05)  # unrealised loss beyond the stop and the daily limit
    pass_(engine, fake)
    closed = positions(client, h, "CLOSED")[0]
    assert closed["exit_reason"] in ("LOSS_LIMIT", "STOP_LOSS")
    with Session(engine) as db:  # a fresh session = state survives a restart
        ds = db.scalar(select(DailyRiskState))
        assert ds.loss_limit_hit_at is not None and ds.realized_net < -150
    fake.set("CE", 24000, 9.95, 10.0)
    again = buy(client, h)
    assert again["status"] == "REJECTED" and "Daily loss stop" in again["reject_reason"]
    risk = client.get("/api/v1/risk", headers=h).json()
    assert risk["new_entries_allowed"] is False
    assert any("Daily loss stop" in r for r in risk["block_reasons"])


def test_profit_lock_blocks_entries_but_keeps_positions(client, h, fake, engine):
    client.patch("/api/v1/risk/limits", headers=h,
                 json={"daily_profit_lock": "100", "max_entries_per_day": 3,
                       "max_open_positions": 2})
    buy(client, h, target_pct=500)
    fake.set("CE", 24000, 13.0, 13.05)
    client.post(f"/api/v1/positions/{positions(client, h)[0]['id']}/exit", headers=h)
    pass_(engine, fake)
    fake.set("CE", 24000, 9.95, 10.0)
    fake.set("PE", 24000, 9.95, 10.0)
    # Profit is booked: the lock trips on the next pass.
    pass_(engine, fake)
    blocked = buy(client, h, option_type="PE")
    assert blocked["status"] == "REJECTED" and "profit lock" in blocked["reject_reason"]


def test_kill_switch_cancels_pending_and_squares_off(client, h, fake, engine):
    client.patch("/api/v1/risk/limits", headers=h, json={"max_open_positions": 2,
                                                          "max_entries_per_day": 3})
    buy(client, h)
    with Session(engine) as db:  # a resting limit entry (limit below the ask)
        uid = db.scalar(select(Order.user_id))
        cache = EX.ChainCache(fake, get_settings())
        o = EX.place_entry(db, get_settings(), cache, user_id=uid, now=utcnow(),
                           option_type="PE", underlying="NIFTY",
                           stop={"type": "PERCENT", "value": "20"}, target=None, trailing=None,
                           client_order_id="rest-limit-1", order_type="LIMIT",
                           limit_offset_pct=D("-5"))
        db.commit()
        assert o.status == "ACKNOWLEDGED"
    r = client.post("/api/v1/risk/kill-switch", headers=h, json={"engaged": True, "confirm": True})
    assert r.status_code == 200
    pass_(engine, fake)  # the worker fills the kill-switch exits
    orders = client.get("/api/v1/orders?status=CANCELLED", headers=h).json()["items"]
    assert [o["client_order_id"] for o in orders] == ["rest-limit-1"]
    closed = positions(client, h, "CLOSED")
    assert closed and closed[0]["exit_reason"] == "KILL_SWITCH"
    assert buy(client, h)["status"] == "REJECTED"


def test_exit_without_quote_waits_and_retries(client, h, fake, engine):
    buy(client, h)
    pid = positions(client, h)[0]["id"]
    fake.set("CE", 24000, None, None)  # one-sided/no quote: market exit is refused
    client.post(f"/api/v1/positions/{pid}/exit", headers=h)
    p = positions(client, h)[0]
    assert p["status"] == "CLOSING"  # exit order rests until a bid appears
    fake.set("CE", 24000, 11.0, 11.05)
    pass_(engine, fake)
    assert positions(client, h, "CLOSED")[0]["id"] == pid


def test_wide_spread_market_order_is_rejected(client, h, fake):
    fake.set("CE", 24000, 8.0, 10.0)
    o = buy(client, h)
    assert o["status"] == "REJECTED" and "Spread" in o["reject_reason"]


# ----------------------------------------------------------- partial fills


def test_partial_fills_charge_brokerage_once(client, h, fake, engine, monkeypatch):
    monkeypatch.setenv("PAPER_MAX_FILL_QTY_PER_PASS", "25")
    get_settings.cache_clear()
    o = buy(client, h)
    assert o["status"] == "PARTIALLY_FILLED" and o["filled_qty"] == 25
    pass_(engine, fake)
    pass_(engine, fake, owner="x1")
    d = client.get(f"/api/v1/orders/{o['id']}", headers=h).json()
    assert d["status"] == "FILLED" and d["filled_qty"] == 75 and len(d["fills"]) == 3
    assert [D(f["charges"]["brokerage"]) for f in d["fills"]] == [D("20.00"), D("0"), D("0")]
    assert positions(client, h)[0]["quantity"] == "75"
    get_settings.cache_clear()


# ------------------------------------------------------- unknown outcomes


def test_timeout_after_broker_accepts_is_reconciled_not_resent(client, h, fake, engine,
                                                              monkeypatch):
    real = paper_mod.PaperBroker.place_order

    def accept_then_timeout(self, req):  # type: ignore[no-untyped-def]
        real(self, req)
        raise BrokerUnavailable("timeout")

    monkeypatch.setattr(paper_mod.PaperBroker, "place_order", accept_then_timeout)
    o = buy(client, h)
    assert o["status"] == "UNKNOWN"
    monkeypatch.setattr(paper_mod.PaperBroker, "place_order", real)
    blocked = buy(client, h)
    assert "outcome is unknown" in blocked["reject_reason"]
    pass_(engine, fake)
    d = client.get(f"/api/v1/orders/{o['id']}", headers=h).json()
    assert d["status"] == "FILLED"
    assert "Reconciled" in " ".join(e["message"] for e in d["events"])
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder)) == 1


def test_timeout_before_broker_sees_it_needs_review(client, h, fake, engine, monkeypatch):
    def timeout(self, req):  # type: ignore[no-untyped-def]
        raise BrokerUnavailable("connection reset")

    monkeypatch.setattr(paper_mod.PaperBroker, "place_order", timeout)
    o = buy(client, h)
    pass_(engine, fake)
    assert client.get(f"/api/v1/orders/{o['id']}", headers=h).json()["status"] == "UNKNOWN"
    pass_(engine, fake, now=utcnow() + timedelta(minutes=3))
    d = client.get(f"/api/v1/orders/{o['id']}", headers=h).json()
    assert d["status"] == "RECONCILIATION_REQUIRED"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder)) == 0  # not resent


def test_illegal_transitions_raise(engine):
    o = Order(status="FILLED", client_order_id="x")
    with Session(engine) as db, pytest.raises(IllegalTransition):
        move(db, o, "ACKNOWLEDGED", "nope")


# -------------------------------------------------------------- bots + risk


def test_two_bots_on_the_same_candle_share_one_position_slot(client, h, fake, engine):
    client.patch("/api/v1/risk/limits", headers=h, json={"max_entries_per_day": 3})
    ids = []
    for name in ("Bot A", "Bot B"):
        b = client.post("/api/v1/bots", headers=h, json={
            "creation_method": "MANUAL", "config": bot_cfg(
                name=name, entry=ALWAYS,
                session={"start_ist": "00:00", "end_ist": "23:59", "cooldown_minutes": 0})}).json()
        client.post(f"/api/v1/bots/{b['id']}/start", headers=h)
        ids.append(b["id"])
    now = utcnow()
    with Session(engine) as db:
        run_once(db, get_settings(), "w1", lambda _db: fake, now)
    with Session(engine) as db:
        statuses = sorted(o.status for o in db.scalars(select(Order)).all())
        assert statuses == ["FILLED", "REJECTED"]
        assert db.scalar(select(func.count()).select_from(Position)) == 1
    logs = [e["message"] for i in ids for e in
            client.get(f"/api/v1/bots/{i}/events", headers=h).json() if e["kind"] == "ORDER"]
    assert any("paper order filled" in m for m in logs)
    assert any("paper entry rejected" in m and "maximum 1" in m for m in logs)
    pos = positions(client, h)[0]
    assert pos["bot_id"] in ids and pos["lots"] == 1


def test_execution_worker_restart_resumes_management(client, h, fake, engine):
    buy(client, h)
    t0 = utcnow()
    assert pass_(engine, fake, now=t0, owner="old") is not None
    assert pass_(engine, fake, now=t0 + timedelta(seconds=10), owner="new") is None  # leased
    fake.set("CE", 24000, 7.0, 7.05)
    assert pass_(engine, fake, now=t0 + timedelta(seconds=70), owner="new") is not None
    assert positions(client, h, "CLOSED")[0]["exit_reason"] == "STOP_LOSS"


def test_square_off_time_when_market_hours_apply(client, h, fake, engine, monkeypatch):
    monkeypatch.setenv("DEMO_MARKET_ALWAYS_OPEN", "false")
    get_settings.cache_clear()
    settings = get_settings()
    morning = datetime.combine(fake.expiry - timedelta(days=1), datetime.min.time(), IST) \
        .replace(hour=11)
    with Session(engine) as db:
        uid = register_user_id(client, h)
        cache = EX.ChainCache(fake, settings)
        o = EX.place_entry(db, settings, cache, user_id=uid, now=morning, option_type="CE",
                           underlying="NIFTY", stop={"type": "PERCENT", "value": "20"},
                           target=None, trailing=None, client_order_id="sq-1")
        EX.fill_now(db, settings, cache, o, morning)
        db.commit()
        late = morning.replace(hour=15, minute=20)
        blocked = EX.place_entry(db, settings, cache, user_id=uid, now=late, option_type="PE",
                                 underlying="NIFTY", stop={"type": "PERCENT", "value": "20"},
                                 target=None, trailing=None, client_order_id="sq-2")
        assert blocked.status == "REJECTED" and "after 15:15" in blocked.reject_reason
        db.commit()
    with Session(engine) as db:
        execution_pass(db, settings, "x", lambda _db: fake, late)
    assert positions(client, h, "CLOSED")[0]["exit_reason"] == "SQUARE_OFF"
    get_settings.cache_clear()


def register_user_id(client, h) -> uuid.UUID:
    return uuid.UUID(client.get("/api/v1/users/me", headers=h).json()["id"])


# ---------------------------------------------------------------- reporting


def test_orders_positions_tradebook_and_isolation(client, h, otp, fake, engine):
    o = buy(client, h)
    pid = positions(client, h)[0]["id"]
    fake.set("CE", 24000, 11.0, 11.05)
    p = client.post(f"/api/v1/positions/{pid}/exit", headers=h).json()
    assert p["status"] == "CLOSED" and p["exit_reason"] == "MANUAL"
    assert client.post(f"/api/v1/positions/{pid}/exit", headers=h).status_code == 409
    executed = client.get("/api/v1/orders?status=EXECUTED", headers=h).json()
    assert executed["total"] == 2
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v1/trades.csv", headers=h).text)))
    assert len(rows) == 1 and rows[0]["mode"] == "PAPER" and rows[0]["exit_reason"] == "MANUAL"
    assert D(rows[0]["net_pnl"]) == D(rows[0]["gross_pnl"]) - D(rows[0]["charges"])
    ch = client.get("/api/v1/portfolio/charges", headers=h).json()
    assert ch["fills"] == 2 and D(ch["breakdown"]["stt"]) > 0
    port = client.get("/api/v1/portfolio", headers=h).json()
    assert D(port["total_net_pnl"]) == D(rows[0]["net_pnl"])
    assert D(port["available_funds"]) == D("10000") + D(rows[0]["net_pnl"])
    other = auth_header(register_and_verify(client, otp, "other@example.com"))
    assert client.get(f"/api/v1/orders/{o['id']}", headers=other).status_code == 404
    assert client.get("/api/v1/positions?state=CLOSED", headers=other).json()["total"] == 0
    assert client.post(f"/api/v1/positions/{pid}/exit", headers=other).status_code == 404


def test_trade_and_risk_alerts_come_from_real_records(client, h, fake, engine):
    client.patch("/api/v1/risk/limits", headers=h,
                 json={"max_risk_per_trade": "200", "daily_loss_limit": "200"})
    buy(client, h)
    fake.set("CE", 24000, 7.0, 7.05)
    pass_(engine, fake)
    fake.set("CE", 24000, 9.95, 10.0)
    buy(client, h)  # rejected: daily loss stop
    trades = client.get("/api/v1/alerts?category=TRADES", headers=h).json()
    titles = [a["title"] for a in trades]
    assert any(t.startswith("Paper BUY filled") for t in titles)
    assert any(t.startswith("Paper SELL filled") for t in titles)
    assert any(t.startswith("Paper BUY rejected") for t in titles)
    risk = client.get("/api/v1/alerts?category=RISK", headers=h).json()
    assert any(a["title"] == "Daily loss stop reached" for a in risk)


def test_risk_exposure_lists_manual_trades(client, h, fake):
    buy(client, h)
    exp = client.get("/api/v1/risk", headers=h).json()["exposure"]
    assert len(exp) == 1
    e = exp[0]
    assert (e["bot_id"], e["bot_name"], e["positions"], e["pending"], e["underlyings"]) == (
        None, None, 1, 0, ["NIFTY"])
    assert D(e["premium"]) == D("10.05") * 75
    assert D(e["planned_risk"]) == (D("10.05") - D("8.00")) * 75


def test_real_demo_provider_fills_with_placeholder_lot(client, h):
    """Regression: DEMO chains flag NO_LOT_SIZE as FAIL; that flag alone must not block
    DEMO paper trades (the placeholder lot covers it), and symbols never use E-notation."""
    chain = client.get("/api/v1/options/NIFTY/chain?strikes=8",
                       headers=h).json()
    strike = next(r["strike"] for r in chain["rows"] if r["is_atm"])
    o = buy(client, h, strike=strike, expiry=chain["expiry"])
    assert o["status"] == "FILLED", o["reject_reason"]
    assert o["lot_size_source"] == "DEMO_PLACEHOLDER" and o["data_source"] == "DEMO"
    assert "E+" not in o["instrument"] and str(int(strike)) in o["instrument"]
