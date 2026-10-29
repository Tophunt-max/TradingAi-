"""Provider-neutral market-data interface and the DEMO implementation.

API routes talk only to `MarketDataProvider`. Every result carries its own
provenance (source, provider, as_of) and a data-quality report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from app.core.time import utcnow
from app.market_data import demo, demo_options
from app.market_data.quality import QualityReport, check_option_chain

UNIVERSE = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "RELIANCE", "TCS", "INFY", "HDFCBANK",
            "ICICIBANK", "SBIN", "ITC", "LT", "BHARTIARTL", "TATAMOTORS"]
OPTION_UNDERLYINGS = ("NIFTY", "BANKNIFTY")


@dataclass
class Result:
    data: Any
    as_of: datetime
    source: str  # DEMO | LIVE | DELAYED
    provider: str
    quality: QualityReport = field(default_factory=QualityReport)
    notes: list[str] = field(default_factory=list)

    @property
    def is_demo(self) -> bool:
        return self.source == "DEMO"

    def provenance(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "provider": self.provider,
            "is_demo": self.is_demo,
            "is_live": self.source == "LIVE",
            "as_of": self.as_of,
            "disclaimer": demo.DEMO_DISCLAIMER if self.is_demo else None,
            "quality": self.quality.as_dict(),
            "notes": self.notes,
        }


class MarketDataProvider(Protocol):
    name: str

    def quotes(self, symbols: list[str] | None, segment: str) -> Result: ...
    def candles(self, symbol: str, timeframe: str, limit: int) -> Result: ...
    def expiries(self, underlying: str) -> Result: ...
    def option_chain(self, underlying: str, expiry: date | None, strikes: int) -> Result: ...
    def search(self, q: str) -> list[dict[str, Any]]: ...
    def status(self) -> dict[str, Any]: ...


class UnknownInstrument(KeyError):
    pass


class DemoProvider:
    name = "DEMO"

    def _r(self, data: Any, now: datetime, notes: list[str] | None = None) -> Result:
        return Result(data=data, as_of=now, source="DEMO", provider=self.name,
                      notes=notes or [])

    def quotes(self, symbols: list[str] | None, segment: str) -> Result:
        now = utcnow()
        out = []
        for sym, inst in demo.INSTRUMENTS.items():
            if symbols is not None and sym not in symbols:
                continue
            if segment != "ALL" and inst.segment != segment:
                continue
            out.append(demo.quote(sym, now))
        return self._r(out, now)

    def candles(self, symbol: str, timeframe: str, limit: int) -> Result:
        if symbol not in demo.INSTRUMENTS:
            raise UnknownInstrument(symbol)
        now = utcnow()
        return self._r(demo.candles(symbol, timeframe, now, limit), now,
                       ["Only closed candles are returned; the forming candle is excluded."])

    def expiries(self, underlying: str) -> Result:
        now = utcnow()
        return self._r(demo_options.demo_expiries(now), now,
                       ["DEMO expiries are illustrative dates, not the exchange calendar."])

    def option_chain(self, underlying: str, expiry: date | None, strikes: int) -> Result:
        now = utcnow()
        valid = demo_options.demo_expiries(now)
        exp = expiry or valid[0]
        if exp not in valid:
            raise ValueError([d.isoformat() for d in valid])
        data = demo_options.chain(underlying, exp, now, strikes)
        data["greeks_model"] = "Black-Scholes (r = 6.5%) on demo IV"
        q = check_option_chain(data["rows"], lot_size=None, expiry_passed=False)
        return Result(data=data, as_of=now, source="DEMO", provider=self.name, quality=q,
                      notes=["Lot size, tick size and expiry calendar come from the instrument "
                             "master only in INDSTOCKS mode."])

    def search(self, q: str) -> list[dict[str, Any]]:
        return [{"symbol": i.symbol, "name": i.name, "exchange": i.exchange, "segment": i.segment,
                 "has_options": i.has_options} for i in demo.search(q)]

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "status": "ok", "detail": "Synthetic data generator."}
