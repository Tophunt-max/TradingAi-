from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.market_data import factory
from app.market_data import quality as q
from app.market_data.indstocks import provider as prov_mod
from app.market_data.indstocks.client import IndstocksClient, IndstocksError, _Limiter
from app.market_data.indstocks.instruments import parse_index_csv, parse_master_csv, sync_source
from app.market_data.indstocks.provider import IndstocksProvider, InstrumentsNotSynced
from app.market_data.session import session_state
from app.market_data.ticks import MemoryTickStore, set_tick_store
from app.models import Instrument, InstrumentSync, User, UserRole
from app.workers.price_feed import FeedStats, handle_message
from tests.conftest import auth_header, register_and_verify
from tests.fake_indstocks import INDEX_CSV, TOKEN, FakeIndstocks, fno_csv

# Wednesday 28 Oct 2026, 10:30 IST (05:00 UTC): market open.
OPEN_NOW = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
EXPIRY = date(2026, 11, 3)


def make_client(fake: FakeIndstocks, token: str | None = TOKEN) -> IndstocksClient:
    return IndstocksClient(base_url="https://api.test", token=token, transport=fake.transport(),
                           sleep=lambda _s: None)


@pytest.fixture(autouse=True)
def _reset():
    prov_mod.reset_cache()
    set_tick_store(None)
    yield
    while _open_sessions:
        _open_sessions.pop().close()
    prov_mod.reset_cache()
    set_tick_store(None)
    factory.client_factory = None


@pytest.fixture
def fake() -> FakeIndstocks:
    return FakeIndstocks(expiry=EXPIRY)


@pytest.fixture
def synced(engine, fake):
    with Session(engine) as db:
        client = make_client(fake)
        for source in ("index", "equity", "fno"):
            assert sync_source(db, client, source).status == "SUCCESS"
    return fake


_open_sessions: list[Session] = []


def provider(engine, fake, now=OPEN_NOW, ticks=None, **settings) -> IndstocksProvider:
    s = Settings(market_data_mode="INDSTOCKS", indstocks_access_token=TOKEN, **settings)
    db = Session(engine)
    _open_sessions.append(db)  # closed by the autouse fixture (PostgreSQL locks otherwise)
    return IndstocksProvider(db, s, make_client(fake), ticks=ticks, now=lambda: now)


# ------------------------------------------------------------------ client


def test_client_sends_raw_token_and_unwraps_data(fake):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"status": "success", "data": {"user_id": "1"}})

    c = IndstocksClient(base_url="https://x", token=TOKEN, transport=httpx.MockTransport(handler))
    assert c.profile() == {"user_id": "1"}
    assert seen["auth"] == TOKEN  # no "Bearer " prefix, per INDstocks conventions


@pytest.mark.parametrize(("status", "body", "kind"), [
    (403, {"status": "error", "message": "bad", "error_type": "TokenException"}, "AUTH"),
    (400, {"message": "authorization not sent in request.", "success": False}, "AUTH"),
    (400, {"message": "Bad Request", "debug_info": "Invalid expiry"}, "BAD_REQUEST"),
])
def test_client_normalises_error_shapes(status, body, kind):
    c = IndstocksClient(base_url="https://x", token=TOKEN, sleep=lambda _s: None,
                        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)))
    with pytest.raises(IndstocksError) as e:
        c.expiries("NIFTY")
    assert e.value.kind == kind
    if "debug_info" in body:
        assert e.value.detail == "Invalid expiry"


def test_client_retries_transient_errors_but_not_client_errors(fake):
    fake.fail_next = [503, 429]
    assert make_client(fake).expiries("NIFTY")
    assert fake.calls.count("/market/instruments/expiries") == 3

    fake.calls.clear()
    with pytest.raises(IndstocksError):
        make_client(fake).option_chain(exchange="NSE", segment="INDEX", underlying_scrip="1",
                                       expiry="2026-11-03", strike_count=3)
    assert len(fake.calls) == 1  # 400 is not retried


def test_client_network_failure_and_missing_token():
    def boom(_r):
        raise httpx.ConnectError("down")

    c = IndstocksClient(base_url="https://x", token=TOKEN, sleep=lambda _s: None,
                        transport=httpx.MockTransport(boom))
    with pytest.raises(IndstocksError) as e:
        c.profile()
    assert e.value.kind == "UNAVAILABLE"
    with pytest.raises(IndstocksError) as e:
        IndstocksClient(base_url="https://x", token=None).profile()
    assert e.value.kind == "NOT_CONFIGURED"


def test_historical_rejects_more_than_five_codes(fake):
    with pytest.raises(ValueError):
        make_client(fake).historical("5minute", [f"NSE_{i}" for i in range(6)], 0, 1)


def test_rate_limiter_waits_after_five_calls_per_second():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    lim = _Limiter(sleep=sleep, clock=lambda: t[0])
    for _ in range(6):
        lim.acquire("data")
    assert slept and sum(slept) >= 0.99


# ------------------------------------------------------------- instruments


def test_index_file_is_parsed_positionally():
    rows, rejected = parse_index_csv(INDEX_CSV + "garbage-line\n")
    by = {r["symbol_name"]: r for r in rows}
    assert by["NIFTY"]["security_id"] == "40000001"
    assert by["BANKNIFTY"]["security_id"] == "40000002"
    assert rejected == 1


def test_fno_rows_without_lot_size_are_rejected():
    rows, rejected = parse_master_csv(fno_csv(EXPIRY), "fno")
    assert rejected == 1 and all(r["lot_size"] for r in rows)
    ce = next(r for r in rows if r["security_id"] == "45108")
    assert (ce["lot_size"], str(ce["tick_size"]), ce["option_type"], ce["expiry_date"]) == (
        75, "0.05", "CE", EXPIRY)


def test_sync_replaces_rows_and_failed_sync_keeps_previous(engine, fake):
    with Session(engine) as db:
        client = make_client(fake)
        sync_source(db, client, "equity")
        sync_source(db, client, "equity")  # idempotent replace
        n = db.scalar(select(func.count()).select_from(Instrument))
        assert n == 3
        fake.token_valid = False
        log = sync_source(db, client, "equity")
        assert log.status == "FAILED" and "Invalid token" in (log.message or "")
        assert db.scalar(select(func.count()).select_from(Instrument)) == 3
        assert db.scalar(select(func.count()).select_from(InstrumentSync)) == 3


# ---------------------------------------------------------------- provider


def test_provider_requires_synced_master(engine, fake):
    with pytest.raises(InstrumentsNotSynced):
        provider(engine, fake).quotes(["RELIANCE"], "ALL")


def test_quotes_map_fields_and_index_spot_from_chain(engine, synced):
    r = provider(engine, synced).quotes(["NIFTY", "RELIANCE", "TCS", "UNKNOWN"], "ALL")
    by = {i["symbol"]: i for i in r.data}
    assert r.source == "LIVE" and r.provider == "INDSTOCKS" and not r.is_demo
    assert by["RELIANCE"]["last"] == 2900.0 and by["RELIANCE"]["prev_close"] == 2871.0
    assert by["RELIANCE"]["change"] == pytest.approx(29.0)
    assert by["NIFTY"]["last"] == 24471.7 and by["NIFTY"]["change"] is None
    assert any("UNKNOWN" in n for n in r.notes)
    assert by["NIFTY"]["has_options"] is True and by["RELIANCE"]["has_options"] is False
    assert [i["symbol"] for i in r.data] == ["NIFTY", "RELIANCE", "TCS"]  # requested order


def test_stale_tick_fails_quality_during_market_hours(engine, synced):
    ticks = MemoryTickStore()
    ticks.put("NSE:2885", 2905.5, int((OPEN_NOW - timedelta(minutes=5)).timestamp() * 1000))
    r = provider(engine, synced, ticks=ticks).quotes(["RELIANCE"], "EQ")
    assert r.data[0]["last"] == 2905.5  # tick overrides the snapshot
    assert r.quality.status == "FAIL"
    assert any(i["code"] == "STALE_QUOTE" for i in r.quality.issues)

    fresh = MemoryTickStore()
    fresh.put("NSE:2885", 2901.0, int(OPEN_NOW.timestamp() * 1000) - 2000)
    prov_mod.reset_cache()
    ok = provider(engine, synced, ticks=fresh).quotes(["RELIANCE"], "EQ")
    assert all(i["code"] != "STALE_QUOTE" for i in ok.quality.issues)


def test_index_candles_use_labelled_futures_proxy_and_drop_forming_candle(engine, synced):
    r = provider(engine, synced).candles("NIFTY", "5m", 50)
    assert any("proxy" in n and "NIFTY26NOVFUT" in n for n in r.notes)
    assert len(r.data) == 50
    assert r.data[-1]["ts"] + timedelta(minutes=5) <= OPEN_NOW
    assert len({c["ts"] for c in r.data}) == len(r.data)
    codes = {i["code"] for i in r.quality.issues}
    assert "DUPLICATES" in codes and "OUTSIDE_SESSION" in codes  # fake data runs 24h


def test_option_chain_sorted_with_master_lot_size(engine, synced):
    r = provider(engine, synced).option_chain("NIFTY", None, 1)
    d = r.data
    assert d["expiry"] == EXPIRY  # past expiry from the API is filtered out
    assert [row["strike"] for row in d["rows"]] == [24400.0, 24450.0, 24500.0]
    assert d["lot_size"] == 75 and d["tick_size"] == 0.05 and d["freeze_qty"] == 1800
    atm = next(row for row in d["rows"] if row["is_atm"])
    assert atm["strike"] == 24450.0
    assert atm["call"]["oi_change"] == 1608490 - 1606988
    assert d["pcr"] is not None
    assert r.quality.status in ("OK", "WARN")
    with pytest.raises(ValueError):
        provider(engine, synced).option_chain("NIFTY", date(2020, 1, 2), 1)


def test_search_uses_instrument_master(engine, synced):
    hits = provider(engine, synced).search("relian")
    assert [h["symbol"] for h in hits] == ["RELIANCE"]  # BSE duplicate excluded


# ----------------------------------------------------------------- quality


def test_quality_detects_gaps_out_of_order_bad_ohlc_and_clock_drift():
    base = datetime(2026, 10, 28, 4, 0, tzinfo=UTC)  # 09:30 IST
    mk = lambda m, o=100.0, h=101.0, low=99.0, c=100.5: {  # noqa: E731
        "ts": base + timedelta(minutes=m), "open": o, "high": h, "low": low, "close": c,
        "volume": 1}
    candles = [mk(10), mk(0), mk(5), mk(25), mk(30, h=90.0)]
    clean, rep = q.check_candles(candles, interval_minutes=5, now=base + timedelta(hours=1),
                                 holidays=[])
    codes = {i["code"] for i in rep.issues}
    assert {"OUT_OF_ORDER", "MISSING_CANDLES", "INVALID_OHLC"} <= codes
    assert [c["ts"] for c in clean] == sorted(c["ts"] for c in clean)
    _, rep2 = q.check_candles([mk(0), mk(600)], interval_minutes=5,
                              now=base + timedelta(minutes=30), holidays=[])
    assert "CLOCK_DRIFT" in {i["code"] for i in rep2.issues}


def test_session_calendar():
    assert session_state(OPEN_NOW, []).is_open
    sat = datetime(2026, 10, 31, 5, 0, tzinfo=UTC)
    assert session_state(sat, []).reason == "Weekend"
    assert session_state(OPEN_NOW, ["2026-10-28"]).reason == "Exchange holiday"
    assert not session_state(OPEN_NOW, []).holidays_configured


def test_option_chain_quality_flags_missing_lot_and_crossed_book():
    rows = [{"call": {"ltp": 10.0, "bid": 11.0, "ask": 10.5}, "put": None}]
    rep = q.check_option_chain(rows, lot_size=None, expiry_passed=False)
    assert rep.status == "FAIL"
    assert {"NO_LOT_SIZE", "CROSSED_BOOK"} <= {i["code"] for i in rep.issues}


# ------------------------------------------------------------------ worker


def test_price_feed_message_handling():
    store, stats = MemoryTickStore(), FeedStats()
    seg = {"2885": "NSE:2885"}
    frame = {"mode": "ltp", "instrument": "2885", "timestamp": 2000, "data": {"ltp": 1426}}
    handle_message(json.dumps(frame), seg, store, stats)
    older = {**frame, "timestamp": 1000, "data": {"ltp": 1}}
    handle_message(json.dumps(json.dumps(older)), seg, store, stats)  # double-encoded
    handle_message(json.dumps({**frame, "timestamp": 2000}), seg, store, stats)  # duplicate
    handle_message(json.dumps({"type": "heartbeat", "timestamp": 3000}), seg, store, stats)
    handle_message("not json", seg, store, stats)
    assert store.get("NSE:2885")[0] == 1426.0
    assert (stats.stored, stats.dropped_stale, stats.malformed) == (1, 2, 1)
    assert store.last_heartbeat() is not None


# ---------------------------------------------------------------------- API


@pytest.fixture
def live_api(monkeypatch, client, otp, engine, fake):
    monkeypatch.setenv("MARKET_DATA_MODE", "INDSTOCKS")
    monkeypatch.setenv("INDSTOCKS_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("INDSTOCKS_DATA_USER_EMAILS", "owner@example.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    factory.client_factory = lambda _s: make_client(fake)
    owner = auth_header(register_and_verify(client, otp, "owner@example.com"))
    yield client, owner
    get_settings.cache_clear()


def test_live_data_is_limited_to_licensed_users(live_api, otp):
    client, owner = live_api
    other = auth_header(register_and_verify(client, otp, "friend@example.com"))
    r = client.get("/api/v1/market/quotes", headers=other)
    assert r.status_code == 403 and r.json()["error"]["code"] == "MARKET_DATA_NOT_LICENSED"
    r = client.get("/api/v1/market/quotes", headers=owner)
    assert r.status_code == 503 and r.json()["error"]["code"] == "INSTRUMENTS_NOT_SYNCED"


def test_admin_sync_then_live_endpoints(live_api, engine):
    client, owner = live_api
    assert client.post("/api/v1/admin/market-data/sync-instruments",
                       headers=owner).status_code == 403
    with Session(engine) as db:
        db.query(User).filter(User.email == "owner@example.com").update(
            {"role": UserRole.ADMIN})
        db.commit()
    r = client.post("/api/v1/admin/market-data/sync-instruments", headers=owner)
    assert r.status_code == 200 and {x["status"] for x in r.json()} == {"SUCCESS"}

    body = client.get("/api/v1/market/quotes?symbols=RELIANCE,TCS", headers=owner).json()
    assert body["source"] == "LIVE" and body["provider"] == "INDSTOCKS"
    assert body["is_demo"] is False and body["disclaimer"] is None
    assert {i["symbol"] for i in body["items"]} == {"RELIANCE", "TCS"}
    assert "quality" in body and body["notes"]

    chain = client.get("/api/v1/options/NIFTY/chain?strikes=3", headers=owner).json()
    assert chain["lot_size"] == 75 and chain["provider"] == "INDSTOCKS"
    assert chain["rows"][0]["call"]["security_id"] == "45106"

    candles = client.get("/api/v1/charts/RELIANCE/candles?timeframe=15m&limit=30"
                         "&indicators=ema9,rsi14", headers=owner).json()
    assert len(candles["candles"]) == 30 and candles["indicators"]["ema9"][-1] is not None

    st = client.get("/api/v1/market/provider-status", headers=owner).json()
    assert st["mode"] == "INDSTOCKS" and st["status"] == "ok"
    assert all(st["instrument_sync"].values())

    sysst = client.get("/api/v1/system/status").json()
    assert any("market data only" in b for b in sysst["live_trading_blockers"])
    assert sysst["live_trading_enabled"] is False


def test_bot_validation_reads_lot_size_from_master(live_api, engine, fake):
    client, owner = live_api
    with Session(engine) as db:
        c = make_client(fake)
        for s in ("index", "equity", "fno"):
            sync_source(db, c, s)
    from tests.test_phase2_api import BOT

    bot = client.post("/api/v1/bots", headers=owner,
                      json={"creation_method": "MANUAL", "config": BOT}).json()
    report = client.post(f"/api/v1/bots/{bot['id']}/validate", headers=owner).json()
    checks = {c["key"]: c for c in report["checks"]}
    assert checks["contract"]["status"] == "PASS"
    assert "lot size 75, tick size 0.05 " in checks["contract"]["message"]
    assert checks["data"]["status"] == "PASS"
    assert checks["engine"]["status"] == "FAIL" and report["can_activate"] is False


def test_expired_token_maps_to_clear_error(live_api, engine, fake):
    client, owner = live_api
    with Session(engine) as db:
        c = make_client(fake)
        for s in ("index", "equity", "fno"):
            sync_source(db, c, s)
    fake.token_valid = False
    r = client.get("/api/v1/market/quotes?symbols=RELIANCE", headers=owner)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "PROVIDER_AUTH_FAILED"
    assert TOKEN not in r.text  # the secret never leaks into responses


def test_production_requires_token_for_indstocks_mode():
    with pytest.raises(ValueError, match="INDSTOCKS_ACCESS_TOKEN"):
        Settings(environment="production", secret_key="x" * 40, otp_delivery="none",
                 database_url="postgresql+psycopg://u@h/db", market_data_mode="INDSTOCKS")
