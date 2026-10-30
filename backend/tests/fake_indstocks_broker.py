"""In-memory fake of the INDstocks account/order endpoints, built from the documented
request/response shapes (https://api-docs.indstocks.com: Users/, portfolio_funds/,
normal_orders/). Used with httpx.MockTransport; never touches the network."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

TOKEN = "fake-indstocks-broker-token-0123456789abcdef"


def order_row(oid: str, remarks: str | None, *, status: str = "SUCCESS", qty: int = 75,
              traded: int = 75, price: str = "43.55", side: str = "BUY") -> dict[str, Any]:
    row = {"created_at": "2026-10-29T09:18:40.446948+05:30",
           "updated_at": "2026-10-29T09:18:40.498595+05:30", "user_id": "710354",
           "security_id": "56998", "isin": "", "name": "NIFTY 3 NOV 24000 CE", "id": oid,
           "exch_order_id": "1300000002340881", "txn_type": side, "exchange": "NSE",
           "segment": "DERIVATIVE", "product": "INTRADAY", "order_type": "MARKET",
           "validity": "DAY", "mkt_type": "NL", "off_mkt_flag": "false", "traded_qty": traded,
           "requested_qty": qty, "requested_price": price, "traded_price": price if traded
           else "", "sl_trigger_price": "", "sl_limit_price": "", "tgt_trigger_price": "",
           "tgt_limit_price": "", "status": status, "extra_info": ""}
    if remarks is not None:
        row["remarks"] = remarks
    return row


@dataclass
class FakeIndstocksBroker:
    token: str = TOKEN
    token_valid: bool = True
    fail_status: int | None = None  # every call answers this HTTP status
    network_down: bool = False
    funds: dict[str, Any] = field(default_factory=lambda: {
        "sod_balance": 4996.47, "pledge_received": 0, "pledge_remained": 0,
        "detailed_avl_balance": {"option_sell": 2980.40, "future": 2980.40,
                                 "option_buy": 4449.65, "comm_option_buy": 2980.40,
                                 "eq_mis": 2980.40, "eq_cnc": 2980.40, "eq_mtf": 2980.40},
        "withdrawal_balance": 2983.47, "funds_added": 0, "funds_withdrawn": 0,
        "realized_pnl": -751.92, "unrealized_pnl": 62.15, "brokerage": 20,
        "eq_charges": 0, "fno_charges": 7.5})
    positions: list[dict[str, Any]] = field(default_factory=lambda: [{
        "position_id": "535654528", "security_id": "56998", "symbol": "NIFTY",
        "segment": "DERIVATIVE", "product": "INTRADAY", "exchange": "NSE",
        "drv_instrument": "OPTIDX", "drv_expiry_date": "11/03/2026 15:30",
        "drv_option_type": "CE", "drv_strike_price": 24000, "net_qty": 75, "avg_price": 43.55,
        "buy_qty": 75, "buy_avg": 43.55, "sell_qty": 0, "sell_avg": 0, "realized_profit": 0.0,
        "day_buy_qty": None, "day_buy_val": None, "day_sell_qty": None, "day_sell_val": None,
        "cf_buy_qty": None, "cf_buy_val": None, "cf_sell_qty": None, "cf_sell_val": None}])
    orders: list[dict[str, Any]] = field(default_factory=list)
    trades: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    seen_tokens: list[str] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def add_order(self, oid: str, remarks: str | None, fills: list[tuple[int, float]] = (),
                  **kw: Any) -> None:
        self.orders.append(order_row(oid, remarks, **kw))
        self.trades[oid] = [{"fill_id": 1279916 + i, "exch_order_id": "1100000017281712",
                             "quantity": q, "price": p, "trade_date": "2026-10-29T09:31:20+05:30"}
                            for i, (q, p) in enumerate(fills)]

    @staticmethod
    def _json(status: int, body: object) -> httpx.Response:
        return httpx.Response(status, content=json.dumps(body),
                              headers={"content-type": "application/json"})

    def _ok(self, data: object) -> httpx.Response:
        return self._json(200, {"status": "success", "data": data})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if self.network_down:
            raise httpx.ConnectError("down", request=request)
        if self.fail_status:
            return self._json(self.fail_status, {"status": "error", "message": "boom",
                                                 "error_type": "ServiceUnavailableException"})
        auth = request.headers.get("authorization")
        if auth is None:
            return self._json(400, {"message": "authorization not sent in request.",
                                    "success": False})
        self.seen_tokens.append(auth)
        if not self.token_valid or auth != self.token:
            return self._json(403, {"status": "error", "message": "Invalid token",
                                    "error_type": "TokenException"})
        body = json.loads(request.content) if request.content else {}
        q = request.url.params
        if (method, path) == ("GET", "/user/profile"):
            return self._ok({"user_id": "1234567", "email": "john.doe@example.com",
                             "first_name": "John", "last_name": "Doe", "demat_id": "",
                             "is_nse_onboarded": True, "is_bse_onboarded": True,
                             "is_nse_fno_onboarded": True, "is_bse_fno_onboarded": True,
                             "ucc": "1ABCDE2N7X", "is_ddpi_active": True})
        if (method, path) == ("GET", "/funds"):
            return self._ok(self.funds)
        if (method, path) == ("GET", "/portfolio/positions"):
            assert q.get("segment") == "derivative"
            return self._ok(self.positions)
        if (method, path) == ("GET", "/order-book"):
            return self._ok(self.orders)
        if (method, path) == ("GET", "/order"):
            assert body.get("segment") == "DERIVATIVE"
            row = next((o for o in self.orders if o["id"] == body.get("order_id")), None)
            if row is None:
                return self._json(400, {"status": "error", "message": "Order not found",
                                        "error_type": "OrderException"})
            return self._ok(row)
        if (method, path) == ("GET", "/order/trades"):
            return self._ok(self.trades.get(body.get("order_id"), []))
        if (method, path) == ("GET", "/trade-book"):
            assert q.get("segment") == "DERIVATIVE"
            out = []
            for o in self.orders:
                for t in self.trades.get(o["id"], []):
                    out.append({**t, "trade_serial_no": "1762843703018658", "scrip_code":
                                o["security_id"], **({"remarks": o["remarks"]}
                                                     if "remarks" in o else {})})
            return self._ok(out)
        if (method, path) == ("POST", "/order"):
            oid = f"DRV-{len(self.orders) + 1}"
            self.add_order(oid, body.get("remarks"), status="INITIATED", traded=0,
                           qty=body["qty"], side=body["txn_type"])
            return self._ok({"order_id": oid, "order_status": "INITIATED"})
        if (method, path) == ("POST", "/order/cancel"):
            for o in self.orders:
                if o["id"] == body["order_id"]:
                    o["status"] = "CANCELLED"
            return self._ok({"order_id": body["order_id"], "order_status": "CANCELLED"})
        return self._json(404, {"status": "error", "message": "not found",
                                "error_type": "NotFoundException"})
