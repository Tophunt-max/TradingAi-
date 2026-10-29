from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.analysis.technical_summary import summarize
from app.indicators import core as ind
from app.market_data import demo, demo_options
from app.options.greeks import black_scholes

NOW = datetime(2026, 10, 29, 6, 7, 30, tzinfo=UTC)


def test_ema_and_sma_known_values():
    vals = [1, 2, 3, 4, 5, 6]
    assert ind.sma(vals, 3) == [None, None, 2, 3, 4, 5]
    e = ind.ema(vals, 3)
    assert e[:2] == [None, None] and e[2] == 2
    assert e[3] == pytest.approx(3.0) and e[5] == pytest.approx(5.0)


def test_rsi_bounds_and_extremes():
    up = [float(i) for i in range(30)]
    assert ind.rsi(up)[-1] == 100.0
    down = list(reversed(up))
    assert ind.rsi(down)[-1] == pytest.approx(0.0)
    closes = [c["close"] for c in demo.candles("NIFTY", "5m", NOW, 200)]
    assert all(0 <= v <= 100 for v in ind.rsi(closes) if v is not None)


@pytest.mark.parametrize("fn", [
    lambda c: ind.ema(c, 9), lambda c: ind.rsi(c, 14), lambda c: ind.macd(c)[2],
    lambda c: ind.bollinger(c)[0],
])
def test_indicators_have_no_lookahead(fn):
    closes = [c["close"] for c in demo.candles("BANKNIFTY", "5m", NOW, 120)]
    full = fn(closes)
    for cut in (40, 80, 119):
        assert fn(closes[:cut]) == full[:cut]


def test_pivots_are_confirmed_only():
    rows = demo.candles("NIFTY", "15m", NOW, 120)
    highs, lows = [r["high"] for r in rows], [r["low"] for r in rows]
    ph, pl = ind.confirmed_pivots(highs, lows, 3, 3)
    assert all(i <= len(rows) - 4 for i, _ in ph + pl)
    # Adding future candles must not remove already-confirmed pivots.
    ph2, pl2 = ind.confirmed_pivots(highs[:100], lows[:100], 3, 3)
    assert set(ph2) <= set(ph) and set(pl2) <= set(pl)


def test_black_scholes_put_call_parity():
    s, k, t, r = 24000.0, 24100.0, 7 / 365, 0.065
    c = black_scholes(spot=s, strike=k, years=t, vol=0.13, rate=r, call=True)
    p = black_scholes(spot=s, strike=k, years=t, vol=0.13, rate=r, call=False)
    assert c.price - p.price == pytest.approx(s - k * math.exp(-r * t), abs=1e-6)
    assert 0 < c.delta < 1 and -1 < p.delta < 0
    assert c.gamma == pytest.approx(p.gamma)
    assert c.theta < 0


def test_demo_candles_exclude_forming_candle_and_are_consistent():
    rows = demo.candles("RELIANCE", "5m", NOW, 50)
    assert len(rows) == 50
    assert rows[-1]["ts"] + timedelta(minutes=5) <= NOW
    for r in rows:
        assert r["low"] <= min(r["open"], r["close"]) <= max(r["open"], r["close"]) <= r["high"]
    assert demo.candles("RELIANCE", "5m", NOW, 50) == rows


def test_demo_option_chain_has_no_invented_contract_metadata():
    exp = demo_options.demo_expiries(NOW)[0]
    data = demo_options.chain("NIFTY", exp, NOW, 5)
    assert data["lot_size"] is None and data["tick_size"] is None
    assert len(data["rows"]) == 11 and sum(r["is_atm"] for r in data["rows"]) == 1
    for r in data["rows"]:
        assert r["call"]["bid"] <= r["call"]["ltp"] <= r["call"]["ask"]
    calls = [r["call"]["ltp"] for r in data["rows"]]
    assert calls == sorted(calls, reverse=True)  # call premium falls as strike rises


def test_summary_never_proposes_trades_without_model():
    out = summarize(demo.candles("NIFTY", "15m", NOW, 150), is_demo=True)
    assert out["decision"] == "NO_TRADE"
    assert out["probabilities"] is None and out["model_version"] is None
    assert any("DEMO" in r for r in out["abstain_reasons"])
    assert all(s < out["last_price"] for s in out["support"])
    assert all(r > out["last_price"] for r in out["resistance"])


def test_inr_formatting_uses_indian_grouping():
    from decimal import Decimal

    from app.services.bots import inr

    assert inr(Decimal("10000.0000")) == "₹10,000"
    assert inr(Decimal("1234567.5")) == "₹12,34,567.50"
    assert inr(Decimal("-250")) == "-₹250"
