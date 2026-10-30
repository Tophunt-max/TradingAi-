"""Package Q: daily report, P&L analytics, CSV exports, persistence, Telegram policy."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import core as ACC
from app.core.config import get_settings
from app.core.time import IST
from app.models import Account, AuditLog, Bot, DailyReport, DailyRiskState, Fill, Order, Position
from app.models.bots import BotCreationMethod
from app.reports import telegram as TG
from app.reports.daily import daily_report
from app.reports.exports import DAILY_COLUMNS, FILL_COLUMNS, LEDGER_COLUMNS, ORDER_COLUMNS
from app.reports.store import generate_daily_reports
from tests.conftest import auth_header, register_and_verify
from tests.test_accounts_isolation import real_account

API = "/api/v1"
DAY = date(2026, 10, 28)  # a Wednesday


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def ch(total: str) -> dict[str, str]:
    t = D(total)
    return {"schedule_id": "test", "brokerage": str(t - 3), "stt": "1", "exchange_txn": "1",
            "sebi": "0", "stamp_duty": "0", "gst": "1", "total": total}


def trade(db: Session, acc: Account, *, underlying="NIFTY", opt="CE", qty=75, entry="10",
          exit_="12", ref="10", ch_in="20", ch_out="20", opened: datetime, closed: datetime,
          reason="TARGET", bot: Bot | None = None) -> Position:
    """A closed position with its entry/exit orders and two fills, inserted directly."""
    common = dict(user_id=acc.user_id, account_id=acc.id, account_mode=acc.mode,
                  underlying=underlying, trading_symbol=f"{underlying}-24000-{opt}",
                  expiry=closed.date() + timedelta(days=2), strike=D("24000"),
                  option_type=opt, lot_size=qty, data_source="LIVE",
                  bot_id=bot.id if bot else None)
    legacy = "PAPER" if acc.mode == "DEMO" else "LIVE"
    eo = Order(**common, client_order_id=uuid.uuid4().hex, broker="PAPER", mode=legacy,
               purpose="ENTRY", side="BUY", lot_size_source="TEST", quantity=qty,
               order_type="MARKET", status="FILLED", filled_qty=qty, avg_fill_price=D(entry),
               risk_snapshot={"entry_price": ref}, created_at=opened, updated_at=opened,
               terminal_at=opened)
    db.add(eo)
    db.flush()
    p = Position(**common, entry_order_id=eo.id, mode=legacy, quantity=0, entry_qty=qty,
                 exit_qty=qty, avg_entry=D(entry), avg_exit=D(exit_), status="CLOSED",
                 exit_reason=reason, realized_gross=(D(exit_) - D(entry)) * qty,
                 charges_total=D(ch_in) + D(ch_out), opened_at=opened, closed_at=closed)
    db.add(p)
    db.flush()
    eo.position_id = p.id
    xo = Order(**common, position_id=p.id, client_order_id=uuid.uuid4().hex, broker="PAPER",
               mode=legacy, purpose="EXIT", side="SELL", lot_size_source="TEST", quantity=qty,
               order_type="MARKET", status="FILLED", filled_qty=qty, avg_fill_price=D(exit_),
               exit_reason=reason, risk_snapshot={"reason": reason}, created_at=closed,
               updated_at=closed, terminal_at=closed)
    db.add(xo)
    db.flush()
    for o, side, px, c, ts in ((eo, "BUY", entry, ch_in, opened), (xo, "SELL", exit_, ch_out,
                                                                   closed)):
        db.add(Fill(order_id=o.id, user_id=acc.user_id, account_id=acc.id,
                    account_mode=acc.mode, broker="PAPER", broker_fill_id=uuid.uuid4().hex,
                    side=side, quantity=qty, price=D(px), ts=ts, charges=ch(c),
                    charges_total=D(c)))
    db.flush()
    return p


@pytest.fixture
def h(client, otp):
    return auth_header(register_and_verify(client, otp))


@pytest.fixture
def book(client, h, engine):
    """DEMO: 3 trades on DAY, one the evening before, one at midnight after; REAL: 1 trade."""
    uid = uuid.UUID(client.get(f"{API}/users/me", headers=h).json()["id"])
    with Session(engine) as db:
        demo = ACC.get_or_create_demo(db, uid)
        bot = Bot(user_id=uid, name="Alpha", strategy_id="ema_cross", underlying="NIFTY",
                  timeframe="5m", creation_method=BotCreationMethod.MANUAL)
        db.add(bot)
        db.flush()
        trade(db, demo, entry="10", exit_="12", ref="9.95", ch_in="22", ch_out="18",
              opened=at(DAY, 9, 30), closed=at(DAY, 10), reason="TARGET", bot=bot)
        trade(db, demo, opt="PE", entry="20", exit_="18", ref="20", ch_in="22", ch_out="20",
              opened=at(DAY, 10, 30), closed=at(DAY, 11), reason="STOP_LOSS")
        trade(db, demo, underlying="BANKNIFTY", qty=30, entry="5", exit_="6", ref="4.90",
              ch_in="10", ch_out="10", opened=at(DAY, 11, 30), closed=at(DAY, 12),
              reason="TARGET", bot=bot)
        prev = DAY - timedelta(days=1)
        trade(db, demo, qty=10, entry="10", exit_="15", ch_in="5", ch_out="5",
              opened=at(prev, 23, 0), closed=at(prev, 23, 59), reason="MANUAL")
        nxt = DAY + timedelta(days=1)
        trade(db, demo, qty=10, entry="10", exit_="8", ch_in="5", ch_out="5",
              opened=at(DAY, 23, 0), closed=at(nxt, 0, 0), reason="TIME_EXIT")
        db.add(DailyRiskState(user_id=uid, account_id=demo.id, account_mode="DEMO",
                              trading_day=DAY, realized_gross=D("30"), charges=D("102"),
                              entries=3, stop_loss_exits=1, loss_limit_hit_at=at(DAY, 11)))
        db.add(AuditLog(user_id=uid, action="risk.kill_switch_engaged", outcome="SUCCESS",
                        details={}, created_at=at(DAY, 11, 5)))
        real = real_account(db, uid)
        trade(db, real, entry="100", exit_="110", ch_in="40", ch_out="60",
              opened=at(DAY, 9, 20), closed=at(DAY, 14), reason="TARGET")
        db.commit()
        return {"uid": uid, "demo": demo.id, "real": real.id}


# ------------------------------------------------------------- daily report


def test_daily_report_numbers_are_exact(client, h, book):
    r = client.get(f"{API}/reports/daily?day={DAY}", headers=h).json()
    assert r["account_mode"] == "DEMO" and r["mode_label"] == "DEMO · VIRTUAL"
    assert r["day"] == "2026-10-28" and r["complete"] is True
    t = r["totals"]
    assert (t["trades"], t["wins"], t["losses"]) == (3, 2, 1)
    assert t["win_rate_pct"] == "66.67"
    assert (t["gross"], t["charges"], t["net"]) == ("30.00", "102.00", "-72.00")
    assert (t["avg_win"], t["avg_loss"]) == ("60.00", "-192.00")
    assert (t["best_trade"], t["worst_trade"]) == ("110.00", "-192.00")
    assert t["profit_factor"] == "0.63"  # 120 / 192
    assert t["max_drawdown"] == "192.00"  # +110 then -192
    assert [x["exit_time_ist"][-6:] for x in r["trades"]] == ["+05:30"] * 3
    # Entries created on DAY: +0.05 x 75, 0 x 75, +0.10 x 30 and the 23:00 entry (0 x 10).
    assert r["slippage"] == {"orders": 4, "total": "6.75", "avg_per_unit": "0.04",
                             "avg_pct": "0.64"}
    by_bot = {g["key"]: (g["trades"], g["net"]) for g in r["by_bot"]}
    assert by_bot == {"Alpha": (2, "120.00"), "Manual": (1, "-192.00")}
    by_strategy = {g["key"]: g["net"] for g in r["by_strategy"]}
    assert by_strategy == {"ema_cross": "120.00", "MANUAL": "-192.00"}
    assert {g["key"]: g["trades"] for g in r["by_exit_reason"]} == {"TARGET": 2, "STOP_LOSS": 1}
    assert [e["type"] for e in r["risk_events"]] == ["LOSS_STOP", "KILL_SWITCH_ENGAGED"]
    assert r["risk_events"][1]["scope"] == "ALL_ACCOUNTS"
    assert r["risk_counters"] == {"entries": 3, "stop_loss_exits": 1, "fill_day_net": "-72.00"}
    # 6 fills of the three trades + the 23:00 entry fill of the trade that closes at midnight.
    assert r["fills"]["count"] == 7


def test_charges_breakdown_sums_to_total(client, h, book):
    r = client.get(f"{API}/reports/daily?day={DAY}", headers=h).json()
    b = r["charges_breakdown"]
    parts = sum(D(b[k]) for k in ("brokerage", "stt", "exchange_txn", "sebi", "stamp_duty",
                                  "gst"))
    assert parts == D(b["total"]) == D(r["totals"]["charges"]) == D("102")
    assert (b["brokerage"], b["stt"], b["gst"]) == ("84.00", "6.00", "6.00")


def test_account_isolation_real_never_in_demo(client, h, book):
    demo = client.get(f"{API}/reports/daily?day={DAY}&account_mode=DEMO", headers=h).json()
    real = client.get(f"{API}/reports/daily?day={DAY}&account_mode=REAL", headers=h).json()
    assert all(t["avg_entry"] != "100.00" for t in demo["trades"])
    assert real["mode_label"] == "REAL · BROKER-CONFIRMED"
    assert real["totals"]["trades"] == 1 and real["totals"]["net"] == "650.00"
    assert real["risk_events"] == [e for e in real["risk_events"]
                                   if e["scope"] == "ALL_ACCOUNTS"]
    pnl = client.get(f"{API}/reports/pnl?from={DAY}&to={DAY}", headers=h).json()
    assert pnl["totals"]["net"] == "-72.00"
    fills = client.get(f"{API}/reports/fills.csv", headers=h).text
    assert "100.0000" not in fills and "REAL" not in fills


def test_pnl_analytics_series_drawdown_and_breakdowns(client, h, book):
    prev, nxt = DAY - timedelta(days=1), DAY + timedelta(days=1)
    r = client.get(f"{API}/reports/pnl?from={prev}&to={nxt}", headers=h).json()
    assert [(d["day"], d["net"], d["cumulative"], d["drawdown"]) for d in r["daily"]] == [
        (str(prev), "40.00", "40.00", "0.00"), (str(DAY), "-72.00", "-32.00", "72.00"),
        (str(nxt), "-30.00", "-62.00", "102.00")]
    assert (r["max_drawdown"], r["current_drawdown"], r["cumulative_net"]) == \
        ("102.00", "102.00", "-62.00")
    assert r["best_day"] == {"day": str(prev), "net": "40.00"}
    assert r["worst_day"] == {"day": str(DAY), "net": "-72.00"}
    assert [(w["key"], w["trades"]) for w in r["by_weekday"]] == [("Tue", 1), ("Wed", 3),
                                                                  ("Thu", 1)]
    inst = {g["key"]: g["net"] for g in r["by_instrument"]}
    assert inst == {"NIFTY CE": "120.00", "NIFTY PE": "-192.00", "BANKNIFTY CE": "10.00"}
    assert {g["key"] for g in r["by_exit_reason"]} == {"TARGET", "STOP_LOSS", "MANUAL",
                                                      "TIME_EXIT"}
    bad = client.get(f"{API}/reports/pnl?from={nxt}&to={prev}", headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_RANGE"
    big = client.get(f"{API}/reports/pnl?from=2024-01-01&to=2026-01-01", headers=h)
    assert big.json()["error"]["code"] == "RANGE_TOO_LARGE"


# --------------------------------------------------------------------- CSV


def rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_csv_exports_columns_ist_and_mode(client, h, book):
    for name, cols in (("orders", ORDER_COLUMNS), ("fills", FILL_COLUMNS),
                       ("ledger", LEDGER_COLUMNS)):
        r = client.get(f"{API}/reports/{name}.csv", headers=h)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert f"quantai-demo-{name}.csv" in r.headers["content-disposition"]
        data = rows(r.text)
        assert data[0] == cols and len(data) > 1
        assert {row[0] for row in data[1:]} == {"DEMO"}
        assert all(row[1].endswith("+05:30") for row in data[1:])
    fills = rows(client.get(f"{API}/reports/fills.csv", headers=h).text)
    assert len(fills) == 1 + 10  # five DEMO trades x 2 fills; the REAL fills never appear
    first = dict(zip(fills[0], fills[1], strict=True))
    assert first["ts_ist"] == "2026-10-27T23:00:00+05:30" and first["side"] == "BUY"
    real_orders = rows(client.get(f"{API}/reports/orders.csv?account_mode=REAL",
                                  headers=h).text)
    assert {row[0] for row in real_orders[1:]} == {"REAL"} and len(real_orders) == 3
    r = client.get(f"{API}/reports/ledger.csv?account_mode=REAL", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LEDGER_DEMO_ONLY"
    d = rows(client.get(f"{API}/reports/daily-report.csv?from={DAY}&to={DAY}",
                        headers=h).text)
    assert d[0] == DAILY_COLUMNS
    assert d[1][:8] == ["DEMO", "2026-10-28", "3", "30.00", "102.00", "-72.00", "-72.00",
                        "72.00"]
    assert d[1][8:] == ["True", "False", "3", "1"]
    legacy = client.get(f"{API}/trades.csv", headers=h).text
    assert client.get(f"{API}/reports/trades.csv", headers=h).text == legacy


def test_reports_require_auth_and_real_without_account(client, otp):
    for p in ("daily", "pnl", "orders.csv", "fills.csv", "ledger.csv", "daily-report.csv",
              "trades.csv"):
        assert client.get(f"{API}/reports/{p}").status_code == 401, p
    hh = auth_header(register_and_verify(client, otp, "solo@example.com"))
    r = client.get(f"{API}/reports/daily?account_mode=REAL", headers=hh).json()
    assert r["account_id"] is None and r["totals"]["trades"] == 0 and "note" in r
    assert rows(client.get(f"{API}/reports/orders.csv?account_mode=REAL",
                           headers=hh).text) == [ORDER_COLUMNS]


# -------------------------------------------------------------- persistence


def test_report_persistence_is_unique_per_account_and_day(client, h, book, engine):
    with Session(engine) as db:
        s1 = generate_daily_reports(db, DAY)
        db.commit()
        s2 = generate_daily_reports(db, DAY)
        db.commit()
        assert (s1["generated"], s1["updated"]) == (2, 0)
        assert (s2["generated"], s2["updated"]) == (0, 2)
        reps = db.scalars(select(DailyReport).where(DailyReport.day == DAY)).all()
        assert sorted(r.account_mode for r in reps) == ["DEMO", "REAL"]
        demo = next(r for r in reps if r.account_mode == "DEMO")
        assert demo.payload["totals"]["net"] == "-72.00"
        dup = DailyReport(user_id=demo.user_id, account_id=demo.account_id,
                          account_mode="DEMO", day=DAY, payload={})
        db.add(dup)
        with pytest.raises(Exception):  # noqa: B017 - IntegrityError on either backend
            db.flush()
        db.rollback()
    r = client.get(f"{API}/reports/daily?day={DAY}", headers=h).json()
    assert r["stored_at"] is not None
    alerts = client.get(f"{API}/alerts?category=SYSTEM", headers=h).json()
    ready = [a for a in alerts if a["title"].startswith("Daily report ready")]
    assert len(ready) == 1 and "DEMO" in ready[0]["body"]  # active mode only


def test_worker_once_generates_reports(client, h, book, engine):
    from app.workers import reports as W

    assert W.main(["--day", DAY.isoformat()]) == 0
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(DailyReport)) == 2
    assert W.default_day(at(DAY, 15, 59)) == DAY - timedelta(days=1)
    assert W.default_day(at(DAY, 16, 1)) == DAY


# ----------------------------------------------------------------- Telegram


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.paths: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.paths.append(request.url.path)
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.MockTransport(handle)


def test_telegram_disabled_when_unset():
    assert TG.TelegramNotifier.from_settings(get_settings()) is None


def test_telegram_sends_redacted_text(client, h, book, engine, monkeypatch, caplog):
    bot_token = "123456:telegram-bot-token-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    get_settings.cache_clear()
    fake = FakeTelegram()
    n = TG.TelegramNotifier.from_settings(get_settings(), transport=fake.transport())
    assert n is not None and bot_token not in repr(n)
    caplog.set_level(logging.DEBUG)
    with Session(engine) as db:
        stats = generate_daily_reports(db, DAY, n, notify_emails=[])
        assert stats["generated"] == 2 and stats["notified"] == 0 and fake.sent == []
        db.execute(DailyReport.__table__.delete())  # start again, now allow-listed
        db.commit()
        stats = generate_daily_reports(db, DAY, n, notify_emails=["TRADER@example.com"])
        db.commit()
    assert stats["notified"] == 2 and fake.paths == [f"/bot{bot_token}/sendMessage"] * 2
    texts = {m["text"] for m in fake.sent}
    demo = next(t for t in texts if "DEMO" in t)
    real = next(t for t in texts if "REAL" in t)
    assert "virtual money" in demo and "net ₹-72.00" in demo and "66.67%" in demo
    assert real == "QuantAI: your REAL account daily report for 2026-10-28 is ready. Open " \
                   "the app."
    for t in texts:
        assert "650" not in t and "indstocks" not in t.lower() and "TESTBROKER" not in t
        assert str(book["real"]) not in t and str(book["uid"]) not in t
    assert all(m["chat_id"] == "-1001" for m in fake.sent)
    assert bot_token not in caplog.text
    assert TG.alert_text("Order 1300000002340881 rejected", "warning") == \
        "QuantAI [WARNING] Order ••• rejected"
    with Session(engine) as db:  # regenerating does not notify again
        assert generate_daily_reports(db, DAY, n, notify_emails=["trader@example.com"]
                                      )["notified"] == 0
    get_settings.cache_clear()


def test_telegram_failure_never_raises(monkeypatch):
    def boom(request):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("down", request=request)

    n = TG.TelegramNotifier("t:x", "1", transport=httpx.MockTransport(boom))
    assert n.send("hello") is False
    n2 = TG.TelegramNotifier("t:x", "1", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"ok": False})))
    assert n2.send("hello") is False


def test_daily_report_function_without_account():
    r = daily_report(None, None, "REAL", DAY)  # type: ignore[arg-type]
    assert r["totals"]["trades"] == 0 and r["charges_breakdown"]["total"] == "0.00"
