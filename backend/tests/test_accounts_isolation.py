"""DEMO / REAL account isolation, the DEMO ledger, demo reset and the execution router."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import core as ACC
from app.bots.engine import run_once
from app.brokers.base import AdapterAccountMismatch, LiveTradingLocked, OrderRequest
from app.brokers.paper import PaperBroker, PaperExecutionAdapter
from app.brokers.real import RealBrokerExecutionAdapter
from app.core.config import get_settings
from app.core.time import utcnow
from app.execution import service as EX
from app.execution.router import route_adapter
from app.models import (
    Account,
    AuditLog,
    DailyRiskState,
    LedgerEntry,
    LiveAuthorization,
    Order,
    PaperBrokerOrder,
    Position,
)
from tests.conftest import alembic_config, auth_header, register_and_verify
from tests.test_phase5_bots import ALWAYS, bot_cfg
from tests.test_phase6_execution import buy, fake, h, pass_, positions  # noqa: F401

API = "/api/v1"


def uid_of(client, h) -> uuid.UUID:  # noqa: F811
    return uuid.UUID(client.get(f"{API}/users/me", headers=h).json()["id"])


def ledger(client, h):  # noqa: F811
    return client.get(f"{API}/accounts/demo/ledger?limit=100", headers=h).json()["items"]


def real_account(db: Session, user_id: uuid.UUID, status: str = "ACTIVE") -> Account:
    acc = Account(user_id=user_id, mode="REAL", status=status, broker="TESTBROKER",
                  opening_balance=None)
    db.add(acc)
    db.flush()
    return acc


def real_rows(db: Session, acc: Account, *, last_price: D = D("50")) -> tuple[Order, Position]:
    """A REAL order + open position inserted directly (no adapter can create them)."""
    now = utcnow()
    common = dict(user_id=acc.user_id, account_id=acc.id, account_mode="REAL",
                  underlying="NIFTY", trading_symbol="REAL-NIFTY-24000-CE",
                  expiry=date.today() + timedelta(days=3), strike=D("24000"),
                  option_type="CE", lot_size=75, data_source="LIVE")
    o = Order(**common, client_order_id=f"REAL-{uuid.uuid4().hex[:12]}", broker="TESTBROKER",
              mode="LIVE", purpose="ENTRY", side="BUY", lot_size_source="INSTRUMENT_MASTER",
              quantity=75, order_type="MARKET", status="FILLED", filled_qty=75,
              avg_fill_price=D("10"), risk_snapshot={}, created_at=now, updated_at=now)
    db.add(o)
    db.flush()
    p = Position(**common, entry_order_id=o.id, mode="LIVE", quantity=75, entry_qty=75,
                 avg_entry=D("10"), last_price=last_price, last_price_at=now, status="OPEN",
                 opened_at=now)
    db.add(p)
    db.flush()
    return o, p


# ----------------------------------------------------------------- creation


def test_new_user_gets_demo_account_with_opening_credit(client, h, engine):  # noqa: F811
    body = client.get(f"{API}/accounts", headers=h).json()
    assert body["active_mode"] == "DEMO" and body["live_trading_enabled"] is False
    demo, real = body["accounts"]
    assert demo["mode"] == "DEMO" and demo["status"] == "ACTIVE" and demo["available"]
    assert D(demo["opening_balance"]) == D(demo["cash"]) == D(demo["equity"]) == D("10000")
    assert real["mode"] == "REAL" and real["status"] == "NOT_CONNECTED"
    assert real["available"] is False and real["cash"] is None and real["equity"] is None
    assert any("No real broker account" in r for r in real["unavailable_reasons"])
    rows = ledger(client, h)
    assert [(e["kind"], D(e["amount"]), D(e["balance_after"])) for e in rows] == [
        ("OPENING_CREDIT", D("10000"), D("10000"))]
    with Session(engine) as db:  # created at registration, not lazily
        assert db.scalar(select(func.count()).select_from(Account)) == 1
        assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 1


def test_missing_demo_account_is_created_lazily(client, h, engine):  # noqa: F811
    with Session(engine) as db:
        db.execute(sa.delete(Account))
        db.commit()
    port = client.get(f"{API}/portfolio", headers=h).json()
    assert port["account_mode"] == "DEMO" and D(port["cash"]) == D("10000")
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Account)) == 1


# ------------------------------------------------------------------- ledger


def test_ledger_debits_and_credits_on_buy_and_sell(client, h, fake):  # noqa: F811
    o = buy(client, h)
    assert o["account_mode"] == "DEMO"
    rows = ledger(client, h)
    kinds = [e["kind"] for e in rows]
    assert kinds == ["CHARGES", "TRADE_BUY", "OPENING_CREDIT"]
    assert D(rows[1]["amount"]) == -D("10.05") * 75
    buy_charges = -D(rows[0]["amount"])
    port = client.get(f"{API}/portfolio", headers=h).json()
    cash = D(rows[0]["balance_after"])
    assert D(port["cash"]) == D(port["available_funds"]) == cash
    assert cash == D("10000") - D("10.05") * 75 - buy_charges
    assert D(port["charges_today"]) == buy_charges
    # Equity marks the open position at its last price (bid 9.95 after the entry fill).
    assert D(port["equity"]) == cash + D(positions(client, h)[0]["ltp"]) * 75

    fake.set("CE", 24000, 11.0, 11.05)
    pid = positions(client, h)[0]["id"]
    client.post(f"{API}/positions/{pid}/exit", headers=h)
    rows = ledger(client, h)
    assert [e["kind"] for e in rows[:2]] == ["CHARGES", "TRADE_SELL"]
    assert D(rows[1]["amount"]) == D("10.95") * 75  # bid 11.00 - 1 tick
    closed = positions(client, h, "CLOSED")[0]
    port = client.get(f"{API}/portfolio", headers=h).json()
    assert D(port["cash"]) == D("10000") + D(closed["realized_net"])
    assert D(port["equity"]) == D(port["cash"])  # nothing open
    assert sum(D(e["amount"]) for e in rows) == D(rows[0]["balance_after"])
    seqs = [e["seq"] for e in rows]
    assert seqs == sorted(seqs, reverse=True) and len(set(seqs)) == len(seqs)


def test_funds_check_uses_ledger_cash(client, h, fake, engine):  # noqa: F811
    with Session(engine) as db:
        acc = ACC.get_or_create_demo(db, uid_of(client, h))
        ACC.post(db, acc, "RESET_ADJUSTMENT", D("-9500"), note="test: drain cash")
        db.commit()
    o = buy(client, h)  # 753.75 premium + charges > 500 cash
    assert o["status"] == "REJECTED" and "available 500.00" in o["reject_reason"]


# -------------------------------------------------------------------- reset


def test_demo_reset_restores_balance_and_never_touches_real(client, h, fake, engine):  # noqa: F811
    client.patch(f"{API}/risk/limits", headers=h, json={"max_open_positions": 2,
                                                         "max_entries_per_day": 3})
    buy(client, h)
    uid = uid_of(client, h)
    with Session(engine) as db:
        cache = EX.ChainCache(fake, get_settings())
        rest = EX.place_entry(db, get_settings(), cache, user_id=uid, now=utcnow(),
                              option_type="PE", underlying="NIFTY",
                              stop={"type": "PERCENT", "value": "20"}, target=None,
                              trailing=None, client_order_id="reset-rest-1", order_type="LIMIT",
                              limit_offset_pct=D("-5"))
        assert rest.status == "ACKNOWLEDGED"
        acc = real_account(db, uid)
        ro, rp = real_rows(db, acc)
        db.commit()
        real_ids = (acc.id, ro.id, rp.id)
        real_before = (ro.status, ro.updated_at, rp.status, rp.quantity, rp.realized_gross)
    fake.set("CE", 24000, 12.0, 12.05)
    pass_(engine, fake)  # marks the demo position at 12.00

    assert client.post(f"{API}/accounts/demo/reset", headers=h,
                       json={"confirm": False}).status_code == 400
    r = client.post(f"{API}/accounts/demo/reset", headers=h, json={"confirm": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["orders_cancelled"] == 1 and out["positions_closed"] == 1
    assert out["reset_count"] == 1 and D(out["account"]["cash"]) == D("10000")
    assert out["account"]["reset_count"] == 1 and out["account"]["last_reset_at"]

    closed = positions(client, h, "CLOSED")[0]
    assert closed["exit_reason"] == "RESET" and D(closed["avg_exit"]) == D("12.00")
    assert positions(client, h) == []
    cancelled = client.get(f"{API}/orders?status=CANCELLED", headers=h).json()["items"]
    assert [o["client_order_id"] for o in cancelled] == ["reset-rest-1"]
    rows = ledger(client, h)
    assert rows[0]["kind"] == "RESET_ADJUSTMENT" and D(rows[0]["balance_after"]) == D("10000")
    assert rows[1]["kind"] == "RESET_CLOSE" and D(rows[1]["amount"]) == D("12.00") * 75
    port = client.get(f"{API}/portfolio", headers=h).json()
    assert port["trades_today"] == 0 and port["open_positions"] == 0
    assert D(port["available_funds"]) == D("10000")
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder).where(
            PaperBrokerOrder.status.in_(("OPEN", "PARTIAL")))) == 0
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "account.demo_reset"))
        assert audit is not None and audit.details["positions_closed"] == 1
        ro, rp = db.get(Order, real_ids[1]), db.get(Position, real_ids[2])
        assert (ro.status, ro.updated_at, rp.status, rp.quantity, rp.realized_gross) == \
            real_before
        assert db.get(Account, real_ids[0]).reset_count == 0
    # A new demo trade works straight after the reset.
    fake.set("CE", 24000, 9.95, 10.0)
    assert buy(client, h)["status"] == "FILLED"


# ------------------------------------------------------------ mode switching


def test_switching_to_real_is_rejected_while_unavailable(client, h, engine):  # noqa: F811
    r = client.post(f"{API}/accounts/active", headers=h, json={"mode": "REAL"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACCOUNT_MODE_UNAVAILABLE"
    assert "No real broker account" in " ".join(r.json()["error"]["details"]["reasons"])
    with Session(engine) as db:  # even with an ACTIVE real account row: live is disabled
        real_account(db, uid_of(client, h))
        db.commit()
    r = client.post(f"{API}/accounts/active", headers=h, json={"mode": "REAL"})
    assert r.status_code == 409
    assert "Live trading is disabled" in " ".join(r.json()["error"]["details"]["reasons"])
    assert client.get(f"{API}/accounts", headers=h).json()["active_mode"] == "DEMO"
    ok = client.post(f"{API}/accounts/active", headers=h, json={"mode": "DEMO"})
    assert ok.status_code == 200 and ok.json()["active_mode"] == "DEMO"
    with Session(engine) as db:
        outcomes = db.scalars(select(AuditLog.outcome).where(
            AuditLog.action == "account.mode_switch").order_by(AuditLog.created_at)).all()
        assert list(outcomes) == ["REJECTED", "REJECTED", "SUCCESS"]
    auth = client.post(f"{API}/accounts/real/authorize", headers=h)
    assert auth.status_code == 409 and auth.json()["error"]["code"] == "LIVE_TRADING_LOCKED"


# ------------------------------------------------------------ adapter routing


def test_demo_order_can_never_reach_the_real_adapter(client, h, engine):  # noqa: F811
    settings = get_settings()
    uid = uid_of(client, h)
    with Session(engine) as db:
        demo = ACC.get_or_create_demo(db, uid)
        real = real_account(db, uid)
        assert isinstance(route_adapter(db, settings, demo), PaperExecutionAdapter)
        assert isinstance(route_adapter(db, settings, real), RealBrokerExecutionAdapter)
        assert PaperBroker is PaperExecutionAdapter
        with pytest.raises(AdapterAccountMismatch):
            RealBrokerExecutionAdapter(db, settings, demo)
        with pytest.raises(AdapterAccountMismatch):
            PaperExecutionAdapter(db, real, lambda c: None)
        req = dict(client_order_id="x-1", side="BUY", quantity=1, order_type="MARKET",
                   limit_price=None, contract={})
        paper = route_adapter(db, settings, demo)
        with pytest.raises(AdapterAccountMismatch):
            paper.submit(OrderRequest(**req, account_id=real.id, account_mode="REAL"))
        with pytest.raises(AdapterAccountMismatch):
            route_adapter(db, settings, real).submit(
                OrderRequest(**req, account_id=demo.id, account_mode="DEMO"))
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder)) == 0


def test_unauthorised_live_order_is_rejected(client, h, fake, engine):  # noqa: F811
    settings = get_settings()
    uid = uid_of(client, h)
    with Session(engine) as db:
        real = real_account(db, uid)
        adapter = route_adapter(db, settings, real)
        req = OrderRequest(client_order_id="live-1", side="BUY", quantity=75,
                           order_type="MARKET", limit_price=None, contract={},
                           account_id=real.id, account_mode="REAL")
        with pytest.raises(LiveTradingLocked) as exc:
            adapter.submit(req)
        assert any("authorisation" in r for r in exc.value.reasons)
        db.add(LiveAuthorization(user_id=uid, account_id=real.id, account_mode="REAL",
                                 consent_text_version="test"))
        db.flush()
        with pytest.raises(LiveTradingLocked) as exc:
            adapter.submit(req)
        assert not any("authorisation" in r for r in exc.value.reasons)
        assert any("LIVE_TRADING_ENABLED" in r for r in exc.value.reasons)
        # Through the full execution path: rejected before any order leaves the server.
        o = EX.place_entry(db, settings, EX.ChainCache(fake, settings), user_id=uid,
                           now=utcnow(), option_type="CE", underlying="NIFTY",
                           stop={"type": "PERCENT", "value": "20"}, target=None,
                           trailing=None, client_order_id="live-entry-1", account=real)
        assert o.status == "REJECTED" and o.account_mode == "REAL"
        assert "LIVE_TRADING_ENABLED" in o.reject_reason
        db.commit()
    r = client.post(f"{API}/orders/paper?account_mode=REAL", headers=h, json={
        "underlying": "NIFTY", "option_type": "CE", "idempotency_key": "live-api-123"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LIVE_TRADING_LOCKED"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PaperBrokerOrder)) == 0


# ------------------------------------------------------------ no contamination


def test_no_cross_mode_pnl_or_risk_contamination(client, h, otp, fake, engine):  # noqa: F811
    buy(client, h)
    uid = uid_of(client, h)
    with Session(engine) as db:
        acc = real_account(db, uid)
        ro, rp = real_rows(db, acc, last_price=D("1"))  # a large REAL unrealised loss
        db.add(DailyRiskState(user_id=uid, account_id=acc.id, account_mode="REAL",
                              trading_day=utcnow().date(), realized_gross=D("-5000"),
                              charges=D("0"), entries=5, stop_loss_exits=0,
                              loss_limit_hit_at=utcnow()))
        db.commit()
        real_order_id, real_pos_id = ro.id, rp.id
    port = client.get(f"{API}/portfolio", headers=h).json()
    assert port["account_mode"] == "DEMO" and port["open_positions"] == 1
    assert port["loss_limit_hit"] is False and port["trades_today"] == 1
    assert D(port["unrealized_pnl"]) == 0  # marked at the fill; the REAL -675 is excluded
    assert D(port["total_net_pnl"]) == -D(port["charges_today"])
    risk = client.get(f"{API}/risk", headers=h).json()
    assert risk["account_mode"] == "DEMO" and risk["usage"]["entries_used"] == 1
    assert not any("loss stop" in r for r in risk["block_reasons"])
    assert [p["account_mode"] for p in positions(client, h)] == ["DEMO"]
    ids = [o["id"] for o in client.get(f"{API}/orders", headers=h).json()["items"]]
    assert str(real_order_id) not in ids
    assert client.get(f"{API}/orders/{real_order_id}", headers=h).status_code == 404
    assert client.post(f"{API}/positions/{real_pos_id}/exit", headers=h).status_code == 404
    real_view = client.get(f"{API}/positions?account_mode=REAL", headers=h).json()["items"]
    assert [p["id"] for p in real_view] == [str(real_pos_id)]
    real_risk = client.get(f"{API}/risk?account_mode=REAL", headers=h).json()
    assert real_risk["usage"]["entries_used"] == 5 and real_risk["new_entries_allowed"] is False
    real_port = client.get(f"{API}/portfolio?account_mode=REAL", headers=h).json()
    assert real_port["cash"] is None and real_port["available_funds"] is None
    # The execution pass manages DEMO only; the REAL row is skipped, not "managed".
    stats = pass_(engine, fake)
    assert stats["real_skipped"] == 1 and stats["accounts"] == 1
    with Session(engine) as db:
        assert db.get(Position, real_pos_id).last_price == D("1")
    # A second demo entry is limited by DEMO usage only (1 open demo position, max 1).
    second = buy(client, h)
    assert second["status"] == "REJECTED" and "1 open position(s)" in second["reject_reason"]


# ----------------------------------------------------------- DB constraints


def test_db_constraints_bind_rows_to_their_account_mode(client, h, engine):  # noqa: F811
    uid = uid_of(client, h)
    with Session(engine) as db:
        demo = ACC.get_or_create_demo(db, uid)
        real = real_account(db, uid)
        db.commit()
        demo_id, real_id = demo.id, real.id

    def fails(make) -> None:  # type: ignore[no-untyped-def]
        with Session(engine) as db:
            db.add(make())
            with pytest.raises(IntegrityError):
                db.flush()

    def order(mode: str, account_id: uuid.UUID) -> Order:
        return Order(user_id=uid, account_id=account_id, account_mode=mode,
                     client_order_id=uuid.uuid4().hex, broker="X", mode="PAPER",
                     purpose="ENTRY", side="BUY", underlying="NIFTY", trading_symbol="X",
                     expiry=date.today(), strike=D("1"), option_type="CE", lot_size=1,
                     lot_size_source="X", quantity=1, order_type="MARKET", data_source="X",
                     risk_snapshot={})

    fails(lambda: order("REAL", demo_id))  # REAL order on a DEMO account
    fails(lambda: order("DEMO", real_id))  # DEMO order on a REAL account
    fails(lambda: LedgerEntry(account_id=real_id, account_mode="REAL", seq=1, kind="X",
                              amount=D("1"), balance_after=D("1")))  # no ledger for REAL
    fails(lambda: Account(user_id=uid, mode="REAL", status="ACTIVE", opening_balance=D("1")))
    fails(lambda: Account(user_id=uid, mode="DEMO", status="ACTIVE",
                          opening_balance=D("10000")))  # one DEMO account per user
    fails(lambda: PaperBrokerOrder(broker_order_id="pb-x", client_order_id="pb-x",
                                   user_id=uid, account_id=real_id, account_mode="REAL",
                                   side="BUY", quantity=1, order_type="MARKET", contract={},
                                   status="OPEN"))
    fails(lambda: LiveAuthorization(user_id=uid, account_id=demo_id, account_mode="DEMO",
                                    consent_text_version="x"))
    with Session(engine) as db:  # the valid combination is accepted
        db.add(order("DEMO", demo_id))
        db.flush()


# ------------------------------------------------------------------- bots


def test_global_limits_per_account_mode_across_two_bots(client, h, fake, engine):  # noqa: F811
    client.patch(f"{API}/risk/limits", headers=h, json={"max_entries_per_day": 3})
    with Session(engine) as db:  # a REAL position must not use up the DEMO slot
        real_rows(db, real_account(db, uid_of(client, h)))
        db.commit()
    for name in ("Bot A", "Bot B"):
        b = client.post(f"{API}/bots", headers=h, json={
            "creation_method": "MANUAL", "config": bot_cfg(
                name=name, entry=ALWAYS,
                session={"start_ist": "00:00", "end_ist": "23:59", "cooldown_minutes": 0})}).json()
        assert b["account_mode"] == "DEMO"
        assert client.post(f"{API}/bots/{b['id']}/start", headers=h).status_code == 200
    with Session(engine) as db:
        run_once(db, get_settings(), "w1", lambda _db: fake, utcnow())
    with Session(engine) as db:
        demo = db.scalars(select(Order).where(Order.account_mode == "DEMO")).all()
        assert sorted(o.status for o in demo) == ["FILLED", "REJECTED"]
        assert all(o.bot_id is not None for o in demo)
        runs = db.execute(sa.text("SELECT account_mode FROM bot_runs")).scalars().all()
        assert runs == ["DEMO", "DEMO"]


def test_bot_start_in_real_mode_is_rejected(client, h, engine):  # noqa: F811
    b = client.post(f"{API}/bots", headers=h, json={
        "creation_method": "MANUAL", "config": bot_cfg(name="Real bot") | {
            "account_mode": "REAL"}}).json()
    assert b["account_mode"] == "REAL" and b["mode"] == "LIVE"
    r = client.post(f"{API}/bots/{b['id']}/start", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LIVE_TRADING_LOCKED"
    assert client.get(f"{API}/bots/{b['id']}", headers=h).json()["status"] == "DRAFT"
    checks = client.post(f"{API}/bots/{b['id']}/validate", headers=h).json()["checks"]
    assert next(c for c in checks if c["key"] == "account_mode")["status"] == "FAIL"
    assert client.get(f"{API}/bots", headers=h).json() == []  # DEMO view
    assert [x["id"] for x in client.get(f"{API}/bots?account_mode=REAL", headers=h).json()] \
        == [b["id"]]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(sa.table("bot_runs"))) == 0


def test_restart_recovery_keeps_ledger_consistent(client, h, fake, engine):  # noqa: F811
    buy(client, h)
    t0 = utcnow()
    assert pass_(engine, fake, now=t0, owner="old") is not None
    fake.set("CE", 24000, 7.0, 7.05)
    assert pass_(engine, fake, now=t0 + timedelta(seconds=70), owner="new") is not None
    closed = positions(client, h, "CLOSED")[0]
    assert closed["exit_reason"] == "STOP_LOSS"
    port = client.get(f"{API}/portfolio", headers=h).json()
    assert D(port["cash"]) == D("10000") + D(closed["realized_net"])


def test_other_users_accounts_are_invisible(client, h, otp, fake):  # noqa: F811
    buy(client, h)
    other = auth_header(register_and_verify(client, otp, "other2@example.com"))
    assert D(client.get(f"{API}/accounts", headers=other).json()["accounts"][0]["cash"]) == \
        D("10000")
    assert len(ledger(client, other)) == 1


# --------------------------------------------------------------- migration


def test_migration_0007_backfills_accounts_and_ledger(engine):
    """Upgrade from 0006 with existing paper data: every row gets the user's new DEMO account
    and the ledger is rebuilt from the recorded fills."""
    cfg = alembic_config(engine)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0006")
    engine.dispose()
    md = sa.MetaData()
    md.reflect(engine)
    t = md.tables
    now = utcnow()
    uid_, bot_, run_, oid_, pid = (uuid.uuid4() for _ in range(5))
    # Reflected SQLite uuid columns are CHAR(32); PostgreSQL takes the uuid itself.
    sqlite = engine.dialect.name == "sqlite"

    def u(x: uuid.UUID):  # type: ignore[no-untyped-def]
        return x.hex if sqlite else x

    uid, bot, run, oid = u(uid_), u(bot_), u(run_), u(oid_)
    with engine.begin() as c:
        c.execute(t["users"].insert().values(
            id=uid, email="old@example.com", full_name="Old", password_hash="x", role="user",
            is_active=True, failed_login_count=0, created_at=now, updated_at=now))
        c.execute(t["bots"].insert().values(
            id=bot, user_id=uid, name="b", strategy_id="momentum", underlying="NIFTY",
            timeframe="5m", creation_method="MANUAL", status="STOPPED", mode="PAPER",
            current_version=1, created_at=now, updated_at=now))
        c.execute(t["bot_runs"].insert().values(
            id=run, bot_id=bot, user_id=uid, config_version=1, mode="PAPER", status="ENDED",
            started_at=now, consecutive_errors=0, signals_count=0))
        common = dict(user_id=uid, underlying="NIFTY", trading_symbol="N", expiry=date.today(),
                      strike=D("24000"), option_type="CE", lot_size=75, data_source="DEMO")
        c.execute(t["orders"].insert().values(
            id=oid, **common, client_order_id="old-1", broker="PAPER", mode="PAPER",
            purpose="ENTRY", side="BUY", lot_size_source="X", quantity=75, order_type="MARKET",
            status="FILLED", filled_qty=75, risk_snapshot={}, created_at=now, updated_at=now))
        c.execute(t["fills"].insert().values(
            id=u(uuid.uuid4()), order_id=oid, user_id=uid, broker="PAPER", broker_fill_id="f1",
            side="BUY", quantity=75, price=D("10"), ts=now, charges={},
            charges_total=D("25")))
        c.execute(t["positions"].insert().values(
            id=u(pid), **common, entry_order_id=oid, mode="PAPER", quantity=75, entry_qty=75,
            exit_qty=0, avg_entry=D("10"), status="OPEN", realized_gross=D("0"),
            charges_total=D("25"), opened_at=now))
        c.execute(t["daily_risk_state"].insert().values(
            id=u(uuid.uuid4()), user_id=uid, trading_day=date.today(), realized_gross=D("0"),
            charges=D("25"), entries=1, stop_loss_exits=0, updated_at=now))
        c.execute(t["paper_broker_orders"].insert().values(
            broker_order_id="PB-1", client_order_id="old-1", user_id=uid, side="BUY",
            quantity=75, order_type="MARKET", contract={}, status="FILLED", filled_qty=75,
            created_at=now, updated_at=now))
        c.execute(t["paper_broker_fills"].insert().values(
            trade_id="PB-1-1", broker_order_id="PB-1", quantity=75, price=D("10"), ts=now))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    engine.dispose()
    with Session(engine) as db:
        acc = db.scalar(select(Account).where(Account.user_id == uid_))
        assert acc.mode == "DEMO" and acc.opening_balance == D("10000")
        assert ACC.cash(db, acc) == D("10000") - D("750") - D("25")
        kinds = db.scalars(select(LedgerEntry.kind).where(LedgerEntry.account_id == acc.id)
                           .order_by(LedgerEntry.seq)).all()
        assert list(kinds) == ["OPENING_CREDIT", "TRADE_BUY", "CHARGES"]
        for table in ("orders", "fills", "positions", "daily_risk_state", "paper_broker_orders",
                      "paper_broker_fills", "bot_runs"):
            rows = db.execute(sa.text(f"SELECT account_id, account_mode FROM {table}")).all()  # noqa: S608
            assert len(rows) == 1 and rows[0][1] == "DEMO", table
            got = rows[0][0]
            assert str(got).replace("-", "") == acc.id.hex, table
        assert db.execute(sa.text("SELECT account_mode FROM bots")).scalar() == "DEMO"
        assert db.execute(sa.text("SELECT active_account_mode FROM user_preferences")
                          ).scalars().all() == []
        assert db.get(Position, pid).account_mode == "DEMO"
