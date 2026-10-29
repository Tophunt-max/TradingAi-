"""Paper execution adapter: the broker simulator for DEMO accounts.

It refuses any account or order whose mode is not DEMO (`AdapterAccountMismatch`), and the
schema backs this up: paper_broker_* rows are CHECKed to account_mode = 'DEMO'.

Keeps its own ledger (paper_broker_orders / paper_broker_fills) so the
execution layer reconciles against it exactly as it would against a real
broker. Fill model:
- BUY at the ask, SELL at the bid, plus `slippage_ticks` against you for
  market orders; limit orders fill only when the touch reaches the limit.
- Market BUY orders are rejected without a two-sided quote or when the spread is
  wider than `max_spread_pct` (protects against illiquid contracts). SELL (exit)
  orders are never rejected for liquidity: they wait for a bid.
- Optional partial fills: at most `max_fill_qty_per_pass` units per pass.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import (
    BrokerError,
    BrokerFill,
    BrokerOrderState,
    ExecutionAdapter,
    OrderRequest,
)
from app.core.time import utcnow
from app.models.execution import PaperBrokerFill, PaperBrokerOrder

QuoteFn = Callable[[dict[str, Any]], dict[str, Any] | None]
D = Decimal


def _tick_round(price: Decimal, tick: Decimal, up: bool) -> Decimal:
    steps = (price / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return max(steps * tick, tick)


class PaperExecutionAdapter(ExecutionAdapter):
    name = "PAPER"
    account_mode = "DEMO"
    supports_live_orders = False

    def __init__(self, db: Session, account: Any, quote_fn: QuoteFn, *,
                 slippage_ticks: int = 1, max_spread_pct: float = 5.0,
                 max_fill_qty_per_pass: int = 0) -> None:
        super().__init__(account)  # raises unless account.mode == "DEMO"
        self.db, self.quote_fn = db, quote_fn
        self.user_id: uuid.UUID = account.user_id
        self.slippage_ticks = slippage_ticks
        self.max_spread_pct = max_spread_pct
        self.max_fill_qty_per_pass = max_fill_qty_per_pass

    # ------------------------------------------------------------ helpers

    def _state(self, o: PaperBrokerOrder) -> BrokerOrderState:
        fills = self.db.scalars(select(PaperBrokerFill).where(
            PaperBrokerFill.broker_order_id == o.broker_order_id)
            .order_by(PaperBrokerFill.ts, PaperBrokerFill.trade_id)).all()
        return BrokerOrderState(
            broker_order_id=o.broker_order_id, client_order_id=o.client_order_id,
            status=o.status, filled_qty=o.filled_qty, avg_price=o.avg_price,  # type: ignore[arg-type]
            reject_reason=o.reject_reason,
            fills=[BrokerFill(f.trade_id, f.quantity, f.price, f.ts) for f in fills])

    def _get(self, broker_order_id: str) -> PaperBrokerOrder:
        o = self.db.get(PaperBrokerOrder, broker_order_id)
        if o is None or o.account_id != self.account.id:
            raise BrokerError(f"Unknown broker order {broker_order_id}.")
        return o

    # ------------------------------------------------------------ adapter API

    def submit(self, req: OrderRequest) -> BrokerOrderState:
        return self.place_order(req)

    def cancel(self, broker_order_id: str) -> BrokerOrderState:
        return self.cancel_order(broker_order_id)

    def poll(self, now: datetime) -> int:
        return self.match(now)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "detail": "Paper simulator (DEMO account)."}

    def place_order(self, req: OrderRequest) -> BrokerOrderState:
        self.check_request(req)  # DEMO orders for this account only
        existing = self.find_by_client_id(req.client_order_id)
        if existing is not None:  # idempotent on client_order_id
            return existing
        if req.quantity <= 0:
            raise BrokerError("Quantity must be positive.")
        if req.order_type == "LIMIT" and (req.limit_price is None or req.limit_price <= 0):
            raise BrokerError("A limit order needs a positive limit price.")
        o = PaperBrokerOrder(
            broker_order_id=f"PB-{uuid.uuid4().hex[:12]}", client_order_id=req.client_order_id,
            user_id=self.user_id, account_id=self.account.id, account_mode="DEMO",
            side=req.side, quantity=req.quantity,
            order_type=req.order_type, limit_price=req.limit_price,
            contract={k: str(v) for k, v in req.contract.items()}, status="OPEN",
            created_at=utcnow(), updated_at=utcnow())
        self.db.add(o)
        self.db.flush()
        return self._state(o)

    def cancel_order(self, broker_order_id: str) -> BrokerOrderState:
        o = self._get(broker_order_id)
        if o.status in ("OPEN", "PARTIAL"):
            o.status, o.updated_at = "CANCELLED", utcnow()
        return self._state(o)

    def get_order(self, broker_order_id: str) -> BrokerOrderState:
        return self._state(self._get(broker_order_id))

    def find_by_client_id(self, client_order_id: str) -> BrokerOrderState | None:
        o = self.db.scalar(select(PaperBrokerOrder).where(
            PaperBrokerOrder.client_order_id == client_order_id,
            PaperBrokerOrder.account_id == self.account.id))
        return self._state(o) if o else None

    def match(self, now: datetime) -> int:
        """Try to fill every open order of this account against current quotes."""
        filled = 0
        rows = self.db.scalars(select(PaperBrokerOrder).where(
            PaperBrokerOrder.account_id == self.account.id,
            PaperBrokerOrder.status.in_(("OPEN", "PARTIAL")))).all()
        for o in rows:
            filled += self._match_one(o, now)
        return filled

    def _match_one(self, o: PaperBrokerOrder, now: datetime) -> int:
        q = self.quote_fn(o.contract)
        if q is None:
            return 0  # market data unavailable: wait, do not guess a price
        bid = D(str(q["bid"])) if q and q.get("bid") else None
        ask = D(str(q["ask"])) if q and q.get("ask") else None
        tick = D(o.contract.get("tick_size") or "0.05")
        if o.side == "SELL":
            # Exits never get dropped: without a bid they wait; a wide spread does not
            # block a protective exit.
            if bid is None or bid <= 0:
                return 0
        elif bid is None or ask is None or bid <= 0 or ask < bid:
            if o.order_type == "MARKET":
                return self._reject(o, "No two-sided quote for this contract.")
            return 0
        mid = (bid + ask) / 2 if ask else bid
        spread_pct = float((ask - bid) / mid * 100) if ask else 0.0
        if o.side == "BUY" and o.order_type == "MARKET" and spread_pct > self.max_spread_pct:
            return self._reject(o, f"Spread {spread_pct:.1f}% is wider than "
                                   f"{self.max_spread_pct}%: market order refused.")
        slip = tick * self.slippage_ticks
        if o.side == "BUY":
            if o.order_type == "LIMIT" and ask > o.limit_price:  # type: ignore[operator]
                return 0
            price = ask + slip if o.order_type == "MARKET" else ask
            price = _tick_round(price, tick, up=True)
        else:
            if o.order_type == "LIMIT" and bid < o.limit_price:  # type: ignore[operator]
                return 0
            price = bid - slip if o.order_type == "MARKET" else bid
            price = _tick_round(price, tick, up=False)
        remaining = o.quantity - o.filled_qty
        qty = remaining if not self.max_fill_qty_per_pass else min(remaining,
                                                                  self.max_fill_qty_per_pass)
        n = len(self._state(o).fills) + 1
        self.db.add(PaperBrokerFill(trade_id=f"{o.broker_order_id}-{n}",
                                    broker_order_id=o.broker_order_id,
                                    account_id=o.account_id, account_mode="DEMO", quantity=qty,
                                    price=price, ts=now))
        prev = (o.avg_price or D("0")) * o.filled_qty
        o.filled_qty += qty
        o.avg_price = ((prev + price * qty) / o.filled_qty).quantize(D("0.0001"))
        o.status = "FILLED" if o.filled_qty == o.quantity else "PARTIAL"
        o.updated_at = now
        self.db.flush()
        return 1

    def _reject(self, o: PaperBrokerOrder, reason: str) -> int:
        if o.filled_qty:
            o.status = "CANCELLED"  # remainder cancelled; filled part stands
        else:
            o.status = "REJECTED"
        o.reject_reason, o.updated_at = reason[:500], utcnow()
        self.db.flush()
        return 0


# Compatibility alias: existing code and tests refer to the simulator as PaperBroker.
PaperBroker = PaperExecutionAdapter
