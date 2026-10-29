"""In-memory fake of the INDstocks REST API, built from the documented shapes
(https://api-docs.indstocks.com). Used with httpx.MockTransport."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

TOKEN = "test-indstocks-token"

INDEX_CSV = """EXCH,SEGMENT,SECURITY_ID
NSE,NIFTY 50,40000001
NSE,NIFTY BANK,40000002
NSE,NIFTY IT,40000004
"""

EQUITY_CSV = """EXCH,SEGMENT,SECURITY_ID,INSTRUMENT_NAME,EXPIRY_CODE,TRADING_SYMBOL,LOT_UNITS,CUSTOM_SYMBOL,EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE,EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SERIES,SYMBOL_NAME,UNDERLYING_SCRIP_NAME,FREEZE_QTY,UPPER_LIMIT,LOWER_LIMIT,ISIN,GENERAL_FACTOR,DELIVERY_UNIT,INTRADAY_LEVERAGE,HAIRCUT_PERCENTAGE,PLEDGE_ELIGIBLE
NSE,E,2885,EQUITY,0,RELIANCE,1,Reliance Industries,,,,0.05,,ES,EQ,RELIANCE,,0,3100,2500,INE002A01018,0,,5,12,true
NSE,E,11536,EQUITY,0,TCS,1,Tata Consultancy,,,,0.05,,ES,EQ,TCS,,0,4400,3600,INE467B01029,0,,5,12,true
BSE,E,500325,EQUITY,0,RELIANCE,1,Reliance Industries,,,,0.05,,ES,A,RELIANCE,,0,3100,2500,INE002A01018,0,,5,12,true
"""

FNO_HEADER = ("EXCH,SEGMENT,SECURITY_ID,INSTRUMENT_NAME,EXPIRY_CODE,TRADING_SYMBOL,LOT_UNITS,"
              "CUSTOM_SYMBOL,EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE,EXPIRY_FLAG,"
              "SEM_EXCH_INSTRUMENT_TYPE,SERIES,SYMBOL_NAME,UNDERLYING_SCRIP_NAME,FREEZE_QTY,"
              "UPPER_LIMIT,LOWER_LIMIT,ISIN,GENERAL_FACTOR,DELIVERY_UNIT")


def fno_csv(expiry: date) -> str:
    e = expiry.isoformat()
    rows = [
        f"NSE,FNO,58072,FUTIDX,1,NIFTY26NOVFUT,75,NIFTY FUT,{e},,,0.05,M,FUTIDX,,NIFTY,NIFTY 50,1800,0,0,,0,",
        f"NSE,FNO,45108,OPTIDX,1,NIFTY-{e}-24450-CE,75,NIFTY 24450 CE,{e},24450,CE,0.05,W,OPTIDX,,NIFTY,NIFTY 50,1800,0,0,,0,",
        f"NSE,FNO,45109,OPTIDX,1,NIFTY-{e}-24450-PE,75,NIFTY 24450 PE,{e},24450,PE,0.05,W,OPTIDX,,NIFTY,NIFTY 50,1800,0,0,,0,",
        f"NSE,FNO,45110,OPTIDX,1,NIFTY-{e}-24500-CE,75,NIFTY 24500 CE,{e},24500,CE,0.05,W,OPTIDX,,NIFTY,NIFTY 50,1800,0,0,,0,",
        f"NSE,FNO,46001,OPTIDX,1,BANKNIFTY-{e}-52000-CE,30,BANKNIFTY 52000 CE,{e},52000,CE,0.05,W,OPTIDX,,BANKNIFTY,NIFTY BANK,900,0,0,,0,",
        # No lot size -> must be rejected by the parser.
        f"NSE,FNO,99999,OPTIDX,1,BROKEN-CE,,BROKEN,{e},100,CE,0.05,W,OPTIDX,,BROKEN,BROKEN,0,0,0,,0,",
    ]
    return FNO_HEADER + "\n" + "\n".join(rows) + "\n"


def chain_payload(expiry: date) -> dict:
    def leg(sid: str, ltp: float, oi: int, prev: int, bid: float, ask: float, delta: float) -> dict:
        return {"security_id": sid, "trading_symbol": f"NIFTY-{sid}", "last_price": ltp,
                "previous_close_price": ltp + 10, "oi": oi, "previous_oi": prev, "volume": 1000,
                "top_bid_price": bid, "top_bid_quantity": 75, "top_ask_price": ask,
                "top_ask_quantity": 75, "iv": 10.5,
                "greeks": {"delta": delta, "gamma": 0.0011, "theta": -10.0, "vega": 13.4}}

    return {"status": "success", "data": {
        "underlying_ltp": 24471.7, "expiry": expiry.isoformat(),
        # Deliberately unsorted keys: the provider must sort numerically.
        "strikes": {
            "24500": {"ce": leg("45110", 140.0, 900, 1000, 139.5, 140.5, 0.48),
                      "pe": leg("45111", 168.0, 700, 600, 167.5, 168.5, -0.52)},
            "24450": {"ce": leg("45108", 167.9, 1608490, 1606988, 166.05, 167.5, 0.56),
                      "pe": leg("45109", 117.65, 1579370, 1579129, 117.65, 119.4, -0.44)},
            "24400": {"ce": leg("45106", 199.0, 500, 500, 198.0, 200.0, 0.62),
                      "pe": leg("45107", 95.0, 800, 900, 94.5, 95.5, -0.38)},
        }}}


@dataclass
class FakeIndstocks:
    expiry: date
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    token_valid: bool = True
    fail_quotes_status: int | None = None
    calls: list[str] = field(default_factory=list)
    fail_next: list[int] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _json(self, status: int, body: object) -> httpx.Response:
        return httpx.Response(status, content=json.dumps(body),
                              headers={"content-type": "application/json"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if self.fail_next:
            return self._json(self.fail_next.pop(0), {"status": "error", "message": "boom",
                                                      "error_type": "NetworkException"})
        if "authorization" not in request.headers:
            return self._json(400, {"message": "authorization not sent in request. Please try "
                                    "again.", "success": False})
        if not self.token_valid or request.headers["authorization"] != TOKEN:
            return self._json(403, {"status": "error", "message": "Invalid token",
                                    "error_type": "TokenException"})
        q = request.url.params
        if path == "/user/profile":
            return self._json(200, {"status": "success", "data": {"user_id": "1"}})
        if path == "/market/instruments":
            text = {"index": INDEX_CSV, "equity": EQUITY_CSV,
                    "fno": fno_csv(self.expiry)}[q["source"]]
            return httpx.Response(200, text=text, headers={"content-type": "text/csv"})
        if path == "/market/quotes/full":
            if self.fail_quotes_status:
                return self._json(self.fail_quotes_status, {"message": "Invalid scrip codes or "
                                                            "mode", "success": False})
            data = {}
            for code in q["scrip-codes"].split(","):
                base = {"NSE_2885": 2900.0, "NSE_11536": 4000.0}.get(code)
                if base is None:
                    return self._json(400, {"message": "Invalid scrip codes or mode",
                                            "success": False})
                data[code] = {"live_price": base, "day_change": 29.0,
                              "day_change_percentage": 1.01, "day_low": base - 20,
                              "day_high": base + 15, "day_open": base - 5,
                              "prev_close": base - 29.0, "volume": 3546732}
            return self._json(200, {"status": "success", "data": data})
        if path.startswith("/market/historical/"):
            interval = path.rsplit("/", 1)[1]
            minutes = {"5minute": 5, "15minute": 15, "60minute": 60, "1day": 1440}[interval]
            code = q["scrip-codes"]
            end = int(q["end_time"]) // 1000
            start = int(q["start_time"]) // 1000
            step = minutes * 60
            first = (end // step) * step  # includes a forming bucket on purpose
            candles, t, price = [], first, 24000.0
            while t >= start and len(candles) < 400:
                price += 3 if (t // step) % 3 else -4
                candles.append({"ts": t, "o": price, "h": price + 6, "l": price - 6,
                                "c": price + 2, "v": 1000})
                t -= step
            candles.reverse()
            candles.append(dict(candles[-1]))  # duplicate bucket on purpose
            return self._json(200, {"success": True, "data": {code: {"candles": candles}}})
        if path == "/market/instruments/expiries":
            past = (self.expiry - timedelta(days=30)).isoformat()
            return self._json(200, {"status": "success",
                                    "data": [past, self.expiry.isoformat(),
                                             (self.expiry + timedelta(days=7)).isoformat()]})
        if path == "/market/option-chain":
            if not q.get("expiry") or q.get("underlying-scrip") not in ("40000001", "40000002"):
                return self._json(400, {"message": "Bad Request", "debug_info":
                                        "Invalid exchange, segment, underlying-scrip or expiry "
                                        "passed"})
            return self._json(200, chain_payload(date.fromisoformat(q["expiry"])))
        return self._json(404, {"status": "error", "message": "not found",
                                "error_type": "NotFoundException"})
