"""Package P: broker-neutral interface, INDstocks adapter (fake transport only), encrypted
broker connections, sync snapshots, reconciliation, permissions. Live stays locked."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.brokers import connections as CONN
from app.brokers import crypto
from app.brokers import registry as REG
from app.brokers.base import BrokerUnavailable, LiveTradingLocked, OrderRequest
from app.brokers.client import BrokerAuthError, BrokerClient, BrokerFunds
from app.brokers.indstocks import IndstocksBrokerClient, normalise_status
from app.brokers.real import RealBrokerExecutionAdapter
from app.brokers.reconcile import reconcile_charges, reconcile_unknown_orders
from app.core.config import get_settings
from app.core.time import utcnow
from app.execution import risk as RISK
from app.execution.router import route_adapter
from app.models import (
    Account,
    AuditLog,
    BrokerChargeReconciliation,
    BrokerConnection,
    BrokerSnapshot,
    Fill,
    LedgerEntry,
    Order,
    Position,
)
from tests.conftest import auth_header, register_and_verify
from tests.fake_indstocks_broker import TOKEN, FakeIndstocksBroker

API = "/api/v1"


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("BROKER_INDSTOCKS_ENABLED", "true")
    get_settings.cache_clear()
    fake = FakeIndstocksBroker()
    monkeypatch.setattr(REG, "transport_override", fake.transport())
    yield fake
    get_settings.cache_clear()


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


def connect(client, h, token=TOKEN):
    return client.post(f"{API}/brokers/indstocks/connect", headers=h, json={"token": token})


def user_id(client, h) -> uuid.UUID:
    return uuid.UUID(client.get(f"{API}/users/me", headers=h).json()["id"])


def real_acc(db: Session, uid: uuid.UUID) -> Account:
    return db.scalar(select(Account).where(Account.user_id == uid, Account.mode == "REAL"))


def real_order(db: Session, acc: Account, status: str = "UNKNOWN") -> Order:
    now = utcnow()
    o = Order(user_id=acc.user_id, account_id=acc.id, account_mode="REAL",
              client_order_id=f"REAL-{uuid.uuid4().hex[:12]}", broker="indstocks", mode="LIVE",
              purpose="ENTRY", side="BUY", underlying="NIFTY", trading_symbol="NIFTY-24000-CE",
              security_id="56998", expiry=date.today() + timedelta(days=3), strike=D("24000"),
              option_type="CE", lot_size=75, lot_size_source="INSTRUMENT_MASTER", quantity=75,
              order_type="MARKET", status=status, data_source="LIVE",
              risk_snapshot={"entry_price": "43.50"}, created_at=now, updated_at=now,
              submitted_at=now)
    db.add(o)
    db.flush()
    return o


# ------------------------------------------------------------------ connect


def test_connect_encrypts_token_and_creates_real_account_without_balance(client, h, broker,
                                                                        engine):
    r = connect(client, h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "indstocks" and body["status"] == "CONNECTED"
    assert body["supports_live_orders"] is False
    assert body["verification"] == "UNVERIFIED_AGAINST_LIVE_API"
    assert body["connection"]["scopes"] == ["profile", "funds", "positions", "orders.read",
                                            "fno"]
    assert broker.seen_tokens == [TOKEN]  # raw token header, no "Bearer", validated once
    uid = user_id(client, h)
    with Session(engine) as db:
        conn = db.scalar(select(BrokerConnection))
        assert conn.token_ciphertext and TOKEN not in conn.token_ciphertext
        assert conn.token_ciphertext != TOKEN
        assert crypto.decrypt(get_settings(), conn.token_ciphertext) == TOKEN
        acc = real_acc(db, uid)
        assert acc.status == "ACTIVE" and acc.broker == "indstocks"
        assert acc.opening_balance is None  # no virtual money on REAL
        assert acc.broker_connection_ref == str(conn.id)
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.account_id == acc.id)) == 0
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "broker.connect"))
        assert audit.outcome == "SUCCESS" and audit.details["scopes"]
    accounts = client.get(f"{API}/accounts", headers=h).json()
    real = next(a for a in accounts["accounts"] if a["mode"] == "REAL")
    assert real["status"] == "ACTIVE" and real["opening_balance"] is None
    assert real["cash"] is None and real["balances_source"] is None  # not synced yet
    assert real["available"] is False  # live trading is locked; REAL mode stays unavailable
    assert client.post(f"{API}/accounts/active", headers=h,
                       json={"mode": "REAL"}).status_code == 409


def test_token_never_in_logs_audit_or_responses(client, h, broker, engine, caplog):
    caplog.set_level(logging.DEBUG)
    texts = [connect(client, h).text]
    texts.append(client.post(f"{API}/brokers/indstocks/sync", headers=h).text)
    texts.append(client.get(f"{API}/brokers/indstocks/health", headers=h).text)
    texts.append(client.get(f"{API}/brokers", headers=h).text)
    texts.append(client.get(f"{API}/brokers/permissions", headers=h).text)
    texts.append(client.get(f"{API}/accounts", headers=h).text)
    texts.append(client.get(f"{API}/alerts", headers=h).text)
    texts.append(client.post(f"{API}/brokers/indstocks/disconnect", headers=h,
                             json={"confirm": True}).text)
    for t in texts:
        assert TOKEN not in t
    assert TOKEN not in caplog.text
    with Session(engine) as db:
        for a in db.scalars(select(AuditLog)).all():
            assert TOKEN not in json.dumps(a.details, default=str)
        for s in db.scalars(select(BrokerSnapshot)).all():
            assert TOKEN not in json.dumps(s.payload)
    assert TOKEN not in repr(IndstocksBrokerClient(token=TOKEN))


def test_rejected_token_stores_nothing(client, h, broker, engine):
    broker.token_valid = False
    r = connect(client, h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "BROKER_TOKEN_REJECTED"
    assert connect(client, h, "short").json()["error"]["code"] == "INVALID_BROKER_TOKEN"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BrokerConnection)) == 0
        assert real_acc(db, user_id(client, h)) is None
        rej = db.scalars(select(AuditLog).where(AuditLog.action == "broker.connect")).all()
        assert [a.outcome for a in rej] == ["REJECTED", "REJECTED"]
    alerts = client.get(f"{API}/alerts?category=SYSTEM", headers=h).json()
    assert any(a["title"] == "Broker connection failed" for a in alerts)


def test_connect_refused_when_disabled_unintegrated_or_without_encryption(client, h, engine,
                                                                         monkeypatch):
    fake = FakeIndstocksBroker()
    monkeypatch.setattr(REG, "transport_override", fake.transport())
    r = connect(client, h)  # BROKER_INDSTOCKS_ENABLED defaults to false
    assert r.status_code == 409 and r.json()["error"]["code"] == "BROKER_NOT_ENABLED"
    r = client.post(f"{API}/brokers/zerodha/connect", headers=h, json={"token": TOKEN})
    assert r.json()["error"]["code"] == "BROKER_NOT_INTEGRATED"
    r = client.post(f"{API}/brokers/nope/connect", headers=h, json={"token": TOKEN})
    assert r.status_code == 404
    monkeypatch.setenv("BROKER_INDSTOCKS_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(crypto, "available", lambda: False)
    r = connect(client, h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "BROKER_ENCRYPTION_UNAVAILABLE"
    assert fake.calls == []  # refused before the token was even sent to the broker
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BrokerConnection)) == 0
    get_settings.cache_clear()


def test_disconnect_revokes_locally(client, h, broker, engine):
    connect(client, h)
    r = client.post(f"{API}/brokers/indstocks/disconnect", headers=h, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    r = client.post(f"{API}/brokers/indstocks/disconnect", headers=h, json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REVOKED" and r.json()["broker_revoke"] == "NOT_SUPPORTED"
    with Session(engine) as db:
        conn = db.scalar(select(BrokerConnection))
        assert conn.status == "REVOKED" and conn.token_ciphertext is None and conn.revoked_at
        acc = real_acc(db, user_id(client, h))
        assert acc.status == "DISCONNECTED" and acc.broker_connection_ref is None
    hl = client.get(f"{API}/brokers/indstocks/health", headers=h).json()
    assert hl["ok"] is False and hl["status"] == "REVOKED" and hl["live_check"] is False
    assert client.post(f"{API}/brokers/indstocks/sync", headers=h).json()["error"]["code"] == \
        "BROKER_NOT_CONNECTED"
    assert client.post(f"{API}/brokers/indstocks/disconnect", headers=h,
                       json={"confirm": True}).status_code == 404
    # Reconnecting re-activates the same REAL account.
    assert connect(client, h).json()["status"] == "CONNECTED"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Account).where(
            Account.mode == "REAL")) == 1


# --------------------------------------------------------------------- sync


def test_sync_snapshots_and_real_funds_from_snapshot_demo_unaffected(client, h, broker, engine):
    connect(client, h)
    before = client.get(f"{API}/accounts", headers=h).json()
    demo_before = next(a for a in before["accounts"] if a["mode"] == "DEMO")
    r = client.post(f"{API}/brokers/indstocks/sync", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["counts"] == {"positions": 1, "orders": 0, "fills": 0}
    with Session(engine) as db:
        snaps = db.scalars(select(BrokerSnapshot)).all()
        assert sorted(s.kind for s in snaps) == ["FILLS", "FUNDS", "ORDERS", "POSITIONS"]
        assert all(s.account_mode == "REAL" for s in snaps)
        funds = next(s for s in snaps if s.kind == "FUNDS").payload
        assert funds["available_option_buy"] == "4449.65"
        pos = next(s for s in snaps if s.kind == "POSITIONS").payload
        assert pos[0]["net_qty"] == 75 and pos[0]["strike"] == "24000"
        uid = user_id(client, h)
        acc = real_acc(db, uid)
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.account_id == acc.id)) == 0
        assert db.scalar(select(BrokerConnection.last_sync_at)) is not None
    after = client.get(f"{API}/accounts", headers=h).json()
    real = next(a for a in after["accounts"] if a["mode"] == "REAL")
    assert D(real["cash"]) == D("4996.47") and D(real["available_funds"]) == D("4449.65")
    assert D(real["unrealized_pnl"]) == D("62.15") and real["equity"] is None
    src = real["balances_source"]
    assert src["source"] == "BROKER_SNAPSHOT" and src["broker"] == "indstocks"
    assert src["fetched_at"]
    demo_after = next(a for a in after["accounts"] if a["mode"] == "DEMO")
    for k in ("cash", "available_funds", "equity", "opening_balance"):
        assert demo_after[k] == demo_before[k]
    assert demo_after["balances_source"]["source"] == "DEMO_LEDGER"
    ledger = client.get(f"{API}/accounts/demo/ledger", headers=h).json()["items"]
    assert [e["kind"] for e in ledger] == ["OPENING_CREDIT"]


def test_sync_failure_marks_error_and_stores_nothing(client, h, broker, engine):
    connect(client, h)
    broker.network_down = True
    r = client.post(f"{API}/brokers/indstocks/sync", headers=h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "BROKER_UNAVAILABLE"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BrokerSnapshot)) == 0
        assert db.scalar(select(BrokerConnection.status)) == "ERROR"
    alerts = client.get(f"{API}/alerts?category=SYSTEM", headers=h).json()
    assert any(a["title"] == "Broker sync failed" for a in alerts)


# ---------------------------------------------------------------- execution


def test_real_adapter_with_connection_is_still_locked(client, h, broker, engine):
    connect(client, h)
    settings = get_settings()
    with Session(engine) as db:
        acc = real_acc(db, user_id(client, h))
        adapter = route_adapter(db, settings, acc)
        assert isinstance(adapter, RealBrokerExecutionAdapter)
        assert adapter.health()["ok"] is True  # connection healthy, yet...
        req = OrderRequest(client_order_id="x-1", side="BUY", quantity=75,
                           order_type="MARKET", limit_price=None,
                           contract={"security_id": "56998"}, account_id=acc.id,
                           account_mode="REAL")
        with pytest.raises(LiveTradingLocked) as e:
            adapter.submit(req)
        assert any("LIVE_TRADING_ENABLED" in r for r in e.value.reasons)
        with pytest.raises(LiveTradingLocked):
            adapter.modify("DRV-1", quantity=75, limit_price=D("40"))
    assert ("POST", "/order") not in broker.calls
    assert all(m == "GET" for m, _ in broker.calls)


def test_reconcile_unknown_order_by_client_id_without_resending(client, h, broker, engine):
    connect(client, h)
    settings = get_settings()
    with Session(engine) as db:
        acc = real_acc(db, user_id(client, h))
        o = real_order(db, acc)
        missing = real_order(db, acc)
        broker.add_order("DRV-77", o.client_order_id, fills=[(50, 43.5), (25, 43.8)])
        out = reconcile_unknown_orders(db, settings, acc)
        assert out == {"checked": 2, "resolved": 1, "not_found": 1, "unavailable": 0}
        assert o.status == "FILLED" and o.broker_order_id == "DRV-77" and o.filled_qty == 75
        fills = db.scalars(select(Fill).where(Fill.order_id == o.id)).all()
        assert sorted(f.broker_fill_id for f in fills) == ["1279916", "1279917"]
        assert all(f.account_mode == "REAL" for f in fills)
        pos = db.scalar(select(Position).where(Position.entry_order_id == o.id))
        assert pos.account_mode == "REAL" and pos.quantity == 75
        assert pos.avg_entry == D("43.6000")  # broker-confirmed fills only
        assert missing.status == "UNKNOWN"  # still within the grace period; not resent
        assert reconcile_unknown_orders(db, settings, acc)["resolved"] == 0
        assert db.scalar(select(func.count()).select_from(Fill)) == 2
        db.commit()
    assert ("POST", "/order") not in broker.calls


def test_reconcile_with_unavailable_broker_counts_and_keeps_unknown(engine, client, h):
    from tests.test_accounts_isolation import real_account

    with Session(engine) as db:
        acc = real_account(db, user_id(client, h))
        o = real_order(db, acc)
        out = reconcile_unknown_orders(db, get_settings(), acc)  # no connection at all
        assert out["unavailable"] == 1 and o.status == "UNKNOWN"


def test_charges_reconciliation_stores_diff(client, h, broker, engine):
    connect(client, h)
    with Session(engine) as db:
        acc = real_acc(db, user_id(client, h))
        o = real_order(db, acc, status="FILLED")
        now = utcnow()
        for i, total in enumerate((D("12.40"), D("6.10"))):
            db.add(Fill(order_id=o.id, user_id=acc.user_id, account_id=acc.id,
                        account_mode="REAL", broker="indstocks", broker_fill_id=f"F{i}",
                        side="BUY", quantity=75, price=D("43.5"), ts=now,
                        charges={"brokerage": "10", "stt": "0", "exchange_txn": "1",
                                 "sebi": "0", "stamp_duty": "0.1", "gst": str(total - D("11.1")),
                                 "total": str(total)}, charges_total=total))
        db.flush()
        funds = BrokerFunds(available_option_buy=None, start_of_day_balance=None,
                            withdrawable=None, realized_pnl=None, unrealized_pnl=None,
                            brokerage_today=D("20"), charges_today=D("7.5"), fetched_at=now)
        rec = reconcile_charges(db, acc, RISK.trading_day(now), funds, "indstocks")
        assert rec.broker_reported == D("27.5") and rec.estimated == D("18.50")
        assert rec.diff == D("9.00") and rec.fills == 2
        assert rec.detail["estimated_breakdown"]["brokerage"] == "20"
        db.commit()
    r = client.post(f"{API}/brokers/indstocks/sync", headers=h).json()  # same day: upsert
    assert r["charges_reconciliation"]["diff"] == "9.0000" or \
        D(r["charges_reconciliation"]["diff"]) == D("9")
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BrokerChargeReconciliation)) == 1


def test_broker_failure_health_error_and_new_real_entries_blocked(client, h, broker, engine):
    connect(client, h)
    broker.fail_status = 503
    hl = client.get(f"{API}/brokers/indstocks/health", headers=h).json()
    assert hl["ok"] is False and hl["status"] == "ERROR" and hl["live_check"] is True
    settings = get_settings()
    with Session(engine) as db:
        assert db.scalar(select(BrokerConnection.status)) == "ERROR"
        acc = real_acc(db, user_id(client, h))
        rs = RISK.lock_user(db, acc.user_id)
        d = RISK.pretrade(db, settings, rs, account=acc, now=utcnow(), lots=1, lot_size=75,
                          entry_price=D("10"), stop_price=D("8"), round_trip_charges=D("40"),
                          buy_charges=D("20"), quote_ok=True, quote_detail="",
                          expiry=date.today() + timedelta(days=3))
        check = next(c for c in d.checks if c["key"] == "broker")
        assert not d.allowed and check["passed"] is False and "ERROR" in check["detail"]
    broker.fail_status = None
    broker.token_valid = False
    hl = client.get(f"{API}/brokers/indstocks/health", headers=h).json()
    assert hl["status"] == "ERROR"  # not CONNECTED any more: no live call until reconnect
    connect_r = connect(client, h)
    assert connect_r.status_code == 400  # the broker now refuses the token


def test_expired_token_detected_by_health(client, h, broker, engine):
    connect(client, h)
    broker.token_valid = False
    hl = client.get(f"{API}/brokers/indstocks/health", headers=h).json()
    assert hl["ok"] is False and hl["status"] == "EXPIRED"
    with Session(engine) as db:
        conn = db.scalar(select(BrokerConnection))
        assert conn.status == "EXPIRED"
        conn.status, conn.token_expires_at = "CONNECTED", utcnow() - timedelta(minutes=1)
        db.commit()
    hl = client.get(f"{API}/brokers/indstocks/health", headers=h).json()
    assert hl["status"] == "EXPIRED" and hl["live_check"] is False


def test_demo_entries_have_no_broker_check(engine, client, h):
    with Session(engine) as db:
        from app.accounts import core as ACC

        acc = ACC.get_or_create_demo(db, user_id(client, h))
        rs = RISK.lock_user(db, acc.user_id)
        d = RISK.pretrade(db, get_settings(), rs, account=acc, now=utcnow(), lots=1,
                          lot_size=1, entry_price=D("10"), stop_price=D("8"),
                          round_trip_charges=D("1"), buy_charges=D("1"), quote_ok=True,
                          quote_detail="", expiry=date.today() + timedelta(days=3))
        assert "broker" not in {c["key"] for c in d.checks}


# -------------------------------------------------------------- permissions


def test_permission_checklist_lists_blockers(client, h, broker):
    p = client.get(f"{API}/brokers/permissions", headers=h).json()
    keys = [c["key"] for c in p["checks"]]
    assert keys == ["broker_connected", "token_valid", "order_permission",
                    "integration_verified", "live_flag", "live_authorization", "risk_limits",
                    "regulatory"]
    assert p["ready"] is False and not any(c["passed"] for c in p["checks"])
    connect(client, h)
    p = client.get(f"{API}/brokers/permissions", headers=h).json()
    passed = {c["key"]: c["passed"] for c in p["checks"]}
    assert passed["broker_connected"] and passed["token_valid"]
    assert not passed["order_permission"] and not passed["integration_verified"]
    assert not passed["live_flag"] and not passed["live_authorization"]
    assert not passed["regulatory"] and not passed["risk_limits"]
    assert p["ready"] is False and len(p["blockers"]) == 6
    assert client.patch(f"{API}/risk/limits", headers=h,
                        json={"max_entries_per_day": 3}).status_code == 200
    p = client.get(f"{API}/brokers/permissions", headers=h).json()
    assert {c["key"]: c["passed"] for c in p["checks"]}["risk_limits"] is True
    assert p["ready"] is False and p["live_trading_enabled"] is False


# ------------------------------------------------------------ access control


def test_endpoints_require_auth_and_are_per_user(client, otp, broker, engine):
    for method, path in (("GET", "/brokers"), ("GET", "/brokers/permissions"),
                         ("POST", "/brokers/indstocks/connect"),
                         ("POST", "/brokers/indstocks/disconnect"),
                         ("POST", "/brokers/indstocks/sync"),
                         ("GET", "/brokers/indstocks/health")):
        assert client.request(method, f"{API}{path}", json={}).status_code == 401, path
    a = auth_header(register_and_verify(client, otp, "a@example.com"))
    b = auth_header(register_and_verify(client, otp, "b@example.com"))
    assert connect(client, a).status_code == 200
    rows = {r["id"]: r for r in client.get(f"{API}/brokers", headers=b).json()}
    assert rows["indstocks"]["status"] == "NOT_CONNECTED" and rows["indstocks"]["connection"] \
        is None
    assert client.post(f"{API}/brokers/indstocks/sync", headers=b).status_code == 409
    assert client.post(f"{API}/brokers/indstocks/disconnect", headers=b,
                       json={"confirm": True}).status_code == 404
    hb = client.get(f"{API}/brokers/indstocks/health", headers=b).json()
    assert hb["status"] == "NOT_CONNECTED"
    real_b = next(x for x in client.get(f"{API}/accounts", headers=b).json()["accounts"]
                  if x["mode"] == "REAL")
    assert real_b["status"] == "NOT_CONNECTED"
    rows_a = {r["id"]: r for r in client.get(f"{API}/brokers", headers=a).json()}
    assert rows_a["indstocks"]["status"] == "CONNECTED"


# ---------------------------------------------------------- client (unit)


def _client(fake: FakeIndstocksBroker, **kw) -> IndstocksBrokerClient:
    return IndstocksBrokerClient(token=fake.token, base_url="https://api.test",
                                 transport=fake.transport(), sleep=lambda s: None, **kw)


def test_indstocks_client_implements_protocol_and_maps_documented_fields():
    fake = FakeIndstocksBroker()
    c = _client(fake)
    assert isinstance(c, BrokerClient)
    f = c.funds()
    assert f.available_option_buy == D("4449.65") and f.start_of_day_balance == D("4996.47")
    assert f.brokerage_today == D("20") and f.charges_today == D("7.5")
    p = c.positions()[0]
    assert p.net_qty == 75 and p.option_type == "CE" and p.strike == D("24000")
    fake.add_order("DRV-1", "tag-1", fills=[(75, 43.55)])
    fake.add_order("DRV-2", None, status="ABORTED", traded=0)
    orders = c.orders()
    assert [(o.broker_order_id, o.client_tag, o.status) for o in orders] == [
        ("DRV-1", "tag-1", "FILLED"), ("DRV-2", None, "REJECTED")]
    st = c.get("DRV-1")
    assert st.status == "FILLED" and st.avg_price == D("43.5500") and st.fills[0].quantity == 75
    assert st.fills[0].ts.utcoffset().total_seconds() == 0  # IST trade_date -> UTC
    assert c.find_by_client_id("tag-1").broker_order_id == "DRV-1"
    assert c.find_by_client_id("missing") is None
    trades = c.tradebook()
    assert trades[0].client_tag == "tag-1" and trades[0].trade_id == "1279916"
    assert c.capabilities.revoke_token is False and c.capabilities.streaming is False
    assert normalise_status("PARTIALLY FILLED - CANCELLED") == "CANCELLED"
    assert normalise_status("SOMETHING-NEW") == "OPEN"  # unknown -> re-read, never "done"


def test_indstocks_place_is_never_retried_and_reads_retry():
    fake = FakeIndstocksBroker(fail_status=503)
    c = _client(fake)
    req = OrderRequest(client_order_id="cid-1", side="BUY", quantity=75, order_type="LIMIT",
                       limit_price=D("43.5"), contract={"security_id": "56998"})
    with pytest.raises(BrokerUnavailable):
        c.place(req)
    assert fake.calls == [("POST", "/order")]  # exactly once: outcome unknown, reconcile
    fake.calls.clear()
    with pytest.raises(BrokerUnavailable):
        c.funds()
    assert len(fake.calls) == 3  # read: initial + 2 retries
    fake.fail_status = None
    st = c.place(req)
    assert st.status == "OPEN" and fake.orders[-1]["remarks"] == "cid-1"
    assert c.cancel(st.broker_order_id).status == "CANCELLED"
    fake.token_valid = False
    with pytest.raises(BrokerAuthError):
        c.profile()
    assert c.health()["status"] == "EXPIRED"


def test_network_errors_are_unavailable_not_rejections():
    def boom(request):  # type: ignore[no-untyped-def]
        raise httpx.ReadTimeout("slow", request=request)

    c = IndstocksBrokerClient(token=TOKEN, base_url="https://x",
                              transport=httpx.MockTransport(boom), sleep=lambda s: None)
    with pytest.raises(BrokerUnavailable):
        c.place(OrderRequest(client_order_id="c", side="BUY", quantity=75,
                             order_type="MARKET", limit_price=None,
                             contract={"security_id": "1"}))
    assert c.health()["status"] == "ERROR"


def test_unreadable_token_marks_error(client, h, broker, engine, monkeypatch):
    connect(client, h)
    with Session(engine) as db:
        conn = db.scalar(select(BrokerConnection))
        conn.token_ciphertext = crypto.PREFIX + "garbage"
        db.commit()
    r = client.post(f"{API}/brokers/indstocks/sync", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "BROKER_TOKEN_UNREADABLE"
    with Session(engine) as db:
        assert db.scalar(select(BrokerConnection.status)) == "ERROR"
    assert CONN.connection_health(None)["status"] == "NOT_CONNECTED"
