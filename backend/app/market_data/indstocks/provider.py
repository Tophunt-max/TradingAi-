"""MarketDataProvider backed by the INDstocks API and the synced instrument master."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import IST, utcnow
from app.market_data.cache import TTLCache
from app.market_data.indstocks.client import IndstocksClient, IndstocksError
from app.market_data.indstocks.instruments import PROVIDER, last_sync
from app.market_data.provider import OPTION_UNDERLYINGS, UNIVERSE, Result, UnknownInstrument
from app.market_data.quality import (
    QualityReport,
    check_candles,
    check_option_chain,
    check_quote_freshness,
    session_note,
)
from app.market_data.ticks import TickStore
from app.models.instruments import Instrument

logger = logging.getLogger("app.indstocks")

INTERVALS = {  # our timeframe -> (INDstocks interval, minutes, max days per call)
    "1m": ("1minute", 1, 7),
    "3m": ("3minute", 3, 7),
    "5m": ("5minute", 5, 7),
    "15m": ("15minute", 15, 7),
    "30m": ("30minute", 30, 7),
    "1h": ("60minute", 60, 15),
    "1d": ("1day", 1440, 365),
}
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}
MAX_PAGES = 4

_cache = TTLCache()


class InstrumentsNotSynced(RuntimeError):
    pass


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class IndstocksProvider:
    name = PROVIDER

    def __init__(
        self,
        db: Session,
        settings: Settings,
        client: IndstocksClient,
        ticks: TickStore | None = None,
        now=utcnow,
    ) -> None:  # type: ignore[no-untyped-def]
        self.db, self.settings, self.client, self.ticks, self.now = db, settings, client, ticks, now
        self._index_codes = dict(
            p.split("=", 1) for p in settings.indstocks_index_quote_codes.split(",") if "=" in p
        )

    # ------------------------------------------------------------ lookups

    def _equity(self, symbol: str) -> Instrument | None:
        rows = self.db.scalars(
            select(Instrument).where(
                Instrument.provider == PROVIDER,
                Instrument.source == "equity",
                Instrument.exchange == "NSE",
                Instrument.symbol_name == symbol,
            )
        ).all()
        if not rows:
            return None
        return next((r for r in rows if r.series == "EQ"), rows[0])

    def _index(self, symbol: str) -> Instrument | None:
        return self.db.scalar(
            select(Instrument)
            .where(
                Instrument.provider == PROVIDER,
                Instrument.source == "index",
                Instrument.symbol_name == symbol,
            )
            .limit(1)
        )

    def _near_future(self, symbol: str) -> Instrument | None:
        today = self.now().astimezone(IST).date()
        return self.db.scalar(
            select(Instrument)
            .where(
                Instrument.provider == PROVIDER,
                Instrument.source == "fno",
                Instrument.symbol_name == symbol,
                Instrument.instrument_name == "FUTIDX",
                Instrument.expiry_date >= today,
            )
            .order_by(Instrument.expiry_date)
            .limit(1)
        )

    def _has_options(self, symbol: str) -> bool:
        return (
            self.db.scalar(
                select(Instrument.id)
                .where(
                    Instrument.provider == PROVIDER,
                    Instrument.source == "fno",
                    Instrument.symbol_name == symbol,
                    Instrument.instrument_name.in_(("OPTIDX", "OPTSTK")),
                )
                .limit(1)
            )
            is not None
        )

    def _require_master(self) -> None:
        if last_sync(self.db, "equity") is None and last_sync(self.db, "fno") is None:
            raise InstrumentsNotSynced(
                "The instrument master has not been synced yet. Run: "
                "python -m app.market_data.sync_instruments"
            )

    # ------------------------------------------------------------ quotes

    def _spot_from_chain(self, symbol: str) -> float | None:
        exps = self.expiries(symbol).data
        if not exps:
            return None
        chain = self._raw_chain(symbol, exps[0], 1)
        return _f(chain.get("underlying_ltp"))

    def quotes(self, symbols: list[str] | None, segment: str) -> Result:
        self._require_master()
        now = self.now()
        report = QualityReport()
        market_open = session_note(report, now, self.settings.market_holidays)
        wanted = symbols or UNIVERSE
        codes: dict[str, tuple[str, Instrument | None]] = {}
        index_spot: dict[str, float | None] = {}
        notes: list[str] = []
        for sym in wanted:
            is_index = sym in INDEX_SYMBOLS
            if segment == "INDEX" and not is_index or segment == "EQ" and is_index:
                continue
            if is_index:
                if sym in self._index_codes:
                    codes[self._index_codes[sym]] = (sym, self._index(sym))
                elif sym in OPTION_UNDERLYINGS:
                    index_spot[sym] = None
                else:
                    notes.append(f"{sym}: no documented INDstocks REST quote for this index.")
            else:
                inst = self._equity(sym)
                if inst is None:
                    notes.append(f"{sym}: not found in the instrument master.")
                    continue
                codes[f"NSE_{inst.security_id}"] = (sym, inst)

        raw = (
            _cache.get_or_set(
                f"q:{','.join(sorted(codes))}", 2.0, lambda: self.client.quotes_full(list(codes))
            )
            if codes
            else {}
        )
        received = self.now()
        items = []
        for code, (sym, inst) in codes.items():
            q = (raw or {}).get(code)
            if not q:
                notes.append(f"{sym}: provider returned no quote.")
                continue
            last, tick_time = _f(q.get("live_price")), None
            if self.ticks:
                tick = self.ticks.get(code.replace("_", ":", 1))
                if tick:
                    last, tick_time = tick
            if tick_time is not None:
                check_quote_freshness(
                    report,
                    quote_time=tick_time,
                    now=now,
                    market_open=market_open,
                    stale_seconds=self.settings.stale_quote_seconds,
                )
            prev = _f(q.get("prev_close"))
            change = (last - prev) if last is not None and prev else _f(q.get("day_change"))
            items.append(
                {
                    "symbol": sym,
                    "name": inst.display_name if inst and inst.display_name else sym,
                    "exchange": "NSE",
                    "segment": "INDEX" if sym in INDEX_SYMBOLS else "EQ",
                    "sector": None,
                    "last": last,
                    "prev_close": prev,
                    "change": change,
                    "change_pct": (change / prev * 100)
                    if change is not None and prev
                    else _f(q.get("day_change_percentage")),
                    "day_high": _f(q.get("day_high")),
                    "day_low": _f(q.get("day_low")),
                    "volume": int(q["volume"]) if q.get("volume") is not None else None,
                    "relative_volume": None,
                    "has_options": self._has_options(sym),
                }
            )
        for sym in index_spot:
            spot = self._spot_from_chain(sym)
            items.append(
                {
                    "symbol": sym,
                    "name": {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}[sym],
                    "exchange": "NSE",
                    "segment": "INDEX",
                    "sector": None,
                    "last": spot,
                    "prev_close": None,
                    "change": None,
                    "change_pct": None,
                    "day_high": None,
                    "day_low": None,
                    "volume": None,
                    "relative_volume": None,
                    "has_options": True,
                }
            )
        order = {s: i for i, s in enumerate(wanted)}
        items.sort(key=lambda it: order.get(it["symbol"], len(order)))
        if index_spot:
            notes.append(
                "Index level comes from the option chain's underlying price; day change "
                "is unavailable without a documented index quote code."
            )
        notes.append("Snapshot time is when QuantAI received it; the REST quote has no timestamp.")
        return Result(
            data=items,
            as_of=received,
            source="LIVE",
            provider=self.name,
            quality=report,
            notes=notes,
        )

    # ------------------------------------------------------------ candles

    def candles(self, symbol: str, timeframe: str, limit: int) -> Result:
        self._require_master()
        interval, minutes, max_days = INTERVALS[timeframe]
        notes: list[str] = []
        if symbol in INDEX_SYMBOLS:
            if symbol in self._index_codes:
                code = self._index_codes[symbol]
            else:
                fut = self._near_future(symbol)
                if fut is None:
                    raise UnknownInstrument(symbol)
                code = f"NFO_{fut.security_id}"
                notes.append(
                    f"Index candles are not documented by INDstocks, so this chart shows "
                    f"{fut.trading_symbol} (near-month futures) as a proxy. Futures trade "
                    f"at a premium/discount to the index."
                )
        else:
            inst = self._equity(symbol)
            if inst is None:
                raise UnknownInstrument(symbol)
            code = f"NSE_{inst.security_id}"

        now = self.now()
        end = now
        collected: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            start = end - timedelta(days=max_days)
            data = _cache.get_or_set(
                f"h:{code}:{interval}:{int(start.timestamp() // 60)}",
                20.0,
                lambda s=start, e=end: self.client.historical(
                    interval, [code], int(s.timestamp() * 1000), int(e.timestamp() * 1000)
                ),
            )
            rows = ((data or {}).get(code) or {}).get("candles") or []
            collected = [
                {
                    "ts": datetime.fromtimestamp(int(c["ts"]), UTC),
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": int(c.get("v") or 0),
                }
                for c in rows
            ] + collected
            if len(collected) >= limit or not rows:
                break
            end = start
        clean, report = check_candles(
            collected, interval_minutes=minutes, now=now, holidays=self.settings.market_holidays
        )
        session_note(report, now, self.settings.market_holidays)
        notes.append("Only closed candles are returned; the forming candle is excluded.")
        return Result(
            data=clean[-limit:],
            as_of=now,
            source="LIVE",
            provider=self.name,
            quality=report,
            notes=notes,
        )

    # ------------------------------------------------------------ options

    def expiries(self, underlying: str) -> Result:
        now = self.now()
        raw = _cache.get_or_set(
            f"exp:{underlying}", 600.0, lambda: self.client.expiries(underlying)
        )
        today = now.astimezone(IST).date()
        dates = sorted(d for d in (date.fromisoformat(x) for x in raw or []) if d >= today)
        return Result(
            data=dates,
            as_of=now,
            source="LIVE",
            provider=self.name,
            notes=["Expiry dates from INDstocks contract data."],
        )

    def _raw_chain(self, underlying: str, expiry: date, strikes: int) -> dict[str, Any]:
        idx = self._index(underlying)
        if idx is None:
            raise InstrumentsNotSynced("Index instrument file not synced (source=index).")
        return _cache.get_or_set(
            f"oc:{underlying}:{expiry}:{strikes}",
            3.0,
            lambda: self.client.option_chain(
                exchange="NSE",
                segment="INDEX",
                underlying_scrip=idx.security_id,
                expiry=expiry.isoformat(),
                strike_count=strikes,
            ),
        )

    def _contract_meta(
        self, security_id: str | None, underlying: str, expiry: date
    ) -> Instrument | None:
        conds = [Instrument.provider == PROVIDER, Instrument.source == "fno"]
        if security_id:
            hit = self.db.scalar(
                select(Instrument).where(*conds, Instrument.security_id == security_id)
            )
            if hit:
                return hit
        return self.db.scalar(
            select(Instrument)
            .where(
                *conds,
                Instrument.symbol_name == underlying,
                Instrument.expiry_date == expiry,
                Instrument.instrument_name == "OPTIDX",
            )
            .limit(1)
        )

    def option_chain(self, underlying: str, expiry: date | None, strikes: int) -> Result:
        self._require_master()
        now = self.now()
        valid = self.expiries(underlying).data
        exp = expiry or (valid[0] if valid else None)
        if exp is None or exp not in valid:
            raise ValueError([d.isoformat() for d in valid])
        raw = self._raw_chain(underlying, exp, strikes)
        spot = _f(raw.get("underlying_ltp"))
        rows, any_sid = [], None
        for strike_key in sorted((raw.get("strikes") or {}), key=float):
            legs = raw["strikes"][strike_key]
            row: dict[str, Any] = {"strike": float(strike_key)}
            for ours, theirs in (("call", "ce"), ("put", "pe")):
                leg = legs.get(theirs)
                if not leg:
                    row[ours] = None
                    continue
                any_sid = any_sid or leg.get("security_id")
                g = leg.get("greeks") or {}
                oi, prev_oi = leg.get("oi"), leg.get("previous_oi")
                row[ours] = {
                    "security_id": leg.get("security_id"),
                    "trading_symbol": leg.get("trading_symbol"),
                    "ltp": _f(leg.get("last_price")),
                    "bid": _f(leg.get("top_bid_price")),
                    "ask": _f(leg.get("top_ask_price")),
                    "iv": _f(leg.get("iv")),
                    "delta": _f(g.get("delta")),
                    "gamma": _f(g.get("gamma")),
                    "theta": _f(g.get("theta")),
                    "vega": _f(g.get("vega")),
                    "oi": int(oi) if oi is not None else None,
                    "oi_change": int(oi) - int(prev_oi)
                    if oi is not None and prev_oi is not None
                    else None,
                    "volume": int(leg["volume"]) if leg.get("volume") is not None else None,
                }
            rows.append(row)
        atm = min(rows, key=lambda r: abs(r["strike"] - spot))["strike"] if rows and spot else None
        for r in rows:
            r["is_atm"] = r["strike"] == atm
        meta = self._contract_meta(any_sid, underlying, exp)
        call_oi = sum((r["call"] or {}).get("oi") or 0 for r in rows)
        put_oi = sum((r["put"] or {}).get("oi") or 0 for r in rows)
        step = rows[1]["strike"] - rows[0]["strike"] if len(rows) > 1 else None
        quality = check_option_chain(
            rows,
            lot_size=meta.lot_size if meta else None,
            expiry_passed=exp < now.astimezone(IST).date(),
        )
        session_note(quality, now, self.settings.market_holidays)
        data = {
            "underlying": underlying,
            "underlying_name": {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}.get(
                underlying, underlying
            ),
            "expiry": exp,
            "spot": spot,
            "atm_strike": atm,
            "strike_step": step,
            "lot_size": meta.lot_size if meta else None,
            "tick_size": float(meta.tick_size) if meta and meta.tick_size else None,
            "freeze_qty": meta.freeze_qty if meta else None,
            "pcr": round(put_oi / call_oi, 2) if call_oi else None,
            "greeks_model": "Provided by INDstocks",
            "rows": rows,
        }
        return Result(
            data=data,
            as_of=now,
            source="LIVE",
            provider=self.name,
            quality=quality,
            notes=["Lot and tick size from the synced instrument master."],
        )

    # ------------------------------------------------------------ misc

    def search(self, q: str) -> list[dict[str, Any]]:
        like = f"%{q.strip().upper()}%"
        rows = self.db.scalars(
            select(Instrument)
            .where(
                Instrument.provider == PROVIDER,
                or_(
                    Instrument.source == "index",
                    (Instrument.source == "equity")
                    & (Instrument.exchange == "NSE")
                    & (Instrument.series == "EQ"),
                ),
                or_(Instrument.symbol_name.like(like), Instrument.display_name.ilike(like)),
            )
            .limit(25)
        ).all()
        ranked = sorted(
            rows,
            key=lambda r: (not (r.symbol_name or "").startswith(q.upper()), r.symbol_name or ""),
        )
        return [
            {
                "symbol": r.symbol_name,
                "name": r.display_name or r.symbol_name,
                "exchange": r.exchange,
                "segment": "INDEX" if r.source == "index" else "EQ",
                "has_options": (r.symbol_name in OPTION_UNDERLYINGS)
                if r.source == "index"
                else self._has_options(r.symbol_name or ""),
            }
            for r in ranked[:10]
        ]

    def status(self) -> dict[str, Any]:
        def probe() -> dict[str, Any]:
            try:
                self.client.profile()
                return {"status": "ok", "detail": "Access token accepted."}
            except IndstocksError as exc:
                return {"status": "down", "kind": exc.kind, "detail": str(exc)}

        out = {"provider": self.name, **_cache.get_or_set("status", 60.0, probe)}
        out["instrument_sync"] = {
            s: (
                ls.finished_at.isoformat()
                if (ls := last_sync(self.db, s)) and ls.finished_at
                else None
            )
            for s in ("equity", "fno", "index")
        }
        hb = self.ticks.last_heartbeat() if self.ticks else None
        out["price_feed_heartbeat"] = hb.isoformat() if hb else None
        return out


def reset_cache() -> None:
    _cache.clear()
