"""Paper execution: contract selection, order placement, fill sync, positions,
exits and reconciliation.

Rules that keep it safe:
- Every entry passes the global risk manager under a per-user lock.
- client_order_id is unique, so a retried request can never create a second
  order; the broker is idempotent on it too.
- A call that times out leaves the order UNKNOWN. It is reconciled by looking
  the order up at the broker, never by sending it again.
- Positions and P&L change only from broker fills.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerError, BrokerOrderState, BrokerUnavailable, OrderRequest
from app.brokers.paper import PaperBroker
from app.core.config import MarketDataMode, Settings
from app.core.time import IST
from app.execution import charges as CH
from app.execution import risk as RISK
from app.execution.state import move
from app.market_data.provider import MarketDataProvider
from app.models.bots import RiskSettings
from app.models.execution import ACTIVE_ORDER_STATES, Fill, Order, OrderEvent, Position

logger = logging.getLogger("app.execution")
D = Decimal
UNKNOWN_GRACE = timedelta(minutes=2)
ENTRY_LIMIT_TTL = timedelta(minutes=5)

QuoteFn = Callable[[dict[str, Any]], dict[str, Any] | None]


class ExecutionError(Exception):
    def __init__(self, message: str, code: str = "EXECUTION_ERROR") -> None:
        super().__init__(message)
        self.message, self.code = message, code


# ------------------------------------------------------------------ market data


class ChainCache:
    """Option-chain lookups shared by one execution pass."""

    def __init__(self, provider: MarketDataProvider, settings: Settings) -> None:
        self.provider, self.settings = provider, settings
        self._chains: dict[tuple[str, date], Any] = {}

    def chain(self, underlying: str, expiry: date) -> Any:
        key = (underlying, expiry)
        if key not in self._chains:
            self._chains[key] = self.provider.option_chain(underlying, expiry, 20)
        return self._chains[key]

    def quote(self, contract: dict[str, Any]) -> dict[str, Any] | None:
        try:
            r = self.chain(contract["underlying"], date.fromisoformat(str(contract["expiry"])))
        except Exception:
            logger.warning("chain_unavailable", extra={"contract": str(contract)})
            return None
        if chain_blocked(r):
            return None
        strike = float(contract["strike"])
        row = next((x for x in r.data["rows"] if abs(float(x["strike"]) - strike) < 1e-6), None)
        leg = row and row.get("call" if contract["option_type"] == "CE" else "put")
        if not leg:
            return None
        return {"bid": leg.get("bid"), "ask": leg.get("ask"), "ltp": leg.get("ltp"),
                "as_of": r.as_of}


def chain_blocked(r: Any) -> bool:
    """True when the chain must not be traded on. DEMO chains never have an exchange lot
    size; that single flag is covered by the labelled DEMO placeholder lot. Every other
    FAIL (expired, negative prices, empty chain, ...) still blocks."""
    fails = [i for i in r.quality.issues if i["severity"] == "FAIL"]
    if r.source == "DEMO":
        fails = [i for i in fails if i["code"] != "NO_LOT_SIZE"]
    return bool(fails)


def strike_text(k: Decimal) -> str:
    """24100 -> '24100', 24050.5 -> '24050.5' (never scientific notation)."""
    return format(k.normalize(), "f")


def resolve_contract(cache: ChainCache, *, underlying: str, option_type: str,
                     strike_selection: str = "ATM", expiry_selection: str = "NEAREST",
                     strike: Decimal | None = None, expiry: date | None = None) -> dict[str, Any]:
    exps = cache.provider.expiries(underlying).data
    if not exps:
        raise ExecutionError("No expiries available.", "NO_CONTRACT")
    if expiry is None:
        expiry = exps[0] if expiry_selection == "NEAREST" or len(exps) < 2 else exps[1]
    elif expiry not in exps:
        raise ExecutionError("Unknown expiry.", "NO_CONTRACT")
    r = cache.chain(underlying, expiry)
    rows = r.data["rows"]
    if strike is None:
        atm = next((i for i, x in enumerate(rows) if x["is_atm"]), None)
        if atm is None:
            raise ExecutionError("No at-the-money strike in the chain.", "NO_CONTRACT")
        step = {"ATM": 0, "ITM_1": -1, "OTM_1": 1, "OTM_2": 2}[strike_selection]
        idx = atm + (step if option_type == "CE" else -step)
        if not 0 <= idx < len(rows):
            raise ExecutionError("Selected strike is outside the chain.", "NO_CONTRACT")
        row = rows[idx]
    else:
        row = next((x for x in rows if abs(float(x["strike"]) - float(strike)) < 1e-6), None)
        if row is None:
            raise ExecutionError("Strike not in the chain.", "NO_CONTRACT")
    leg = row.get("call" if option_type == "CE" else "put") or {}
    demo = r.source == "DEMO"
    lot_size = r.data.get("lot_size") or (cache.settings.demo_lot_size if demo else None)
    if not lot_size:
        raise ExecutionError("Lot size is not available from the instrument master.",
                             "NO_LOT_SIZE")
    tick = r.data.get("tick_size") or (0.05 if demo else None)
    if not tick:
        raise ExecutionError("Tick size is not available from the instrument master.",
                             "NO_TICK_SIZE")
    k = D(str(row["strike"]))
    return {
        "underlying": underlying, "expiry": expiry, "strike": k, "option_type": option_type,
        "security_id": leg.get("security_id"),
        "trading_symbol": leg.get("trading_symbol")
        or f"{underlying} {expiry:%d%b%y} {strike_text(k)} {option_type}".upper(),
        "lot_size": int(lot_size),
        "lot_size_source": "DEMO_PLACEHOLDER" if demo else "INSTRUMENT_MASTER",
        "tick_size": D(str(tick)), "bid": leg.get("bid"), "ask": leg.get("ask"),
        "ltp": leg.get("ltp"), "quality": "FAIL" if chain_blocked(r)
        else "WARN" if r.quality.status == "FAIL" else r.quality.status,
        "source": r.source,
    }


def _contract_dict(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c[k] for k in ("underlying", "expiry", "strike", "option_type", "security_id",
                              "tick_size")}


def broker_for(db: Session, settings: Settings, user_id: uuid.UUID,
               cache: ChainCache) -> PaperBroker:
    return PaperBroker(db, user_id, cache.quote, slippage_ticks=settings.paper_slippage_ticks,
                       max_spread_pct=settings.paper_max_spread_pct,
                       max_fill_qty_per_pass=settings.paper_max_fill_qty_per_pass)


# ------------------------------------------------------------------- levels


def _level(entry: Decimal, spec: dict[str, Any] | None, down: bool,
           tick: Decimal) -> Decimal | None:
    if not spec:
        return None
    v = D(str(spec["value"]))
    raw = entry * (1 - v / 100) if spec["type"] == "PERCENT" and down else \
        entry * (1 + v / 100) if spec["type"] == "PERCENT" else \
        entry - v if down else entry + v
    steps = (raw / tick).to_integral_value(rounding=ROUND_FLOOR)
    return max(steps * tick, tick)


# -------------------------------------------------------------------- entry


def place_entry(db: Session, settings: Settings, cache: ChainCache, *, user_id: uuid.UUID,
                now: datetime, option_type: str, underlying: str, stop: dict[str, Any],
                target: dict[str, Any] | None, trailing: dict[str, Any] | None,
                client_order_id: str, lots: int | None = None,
                strike_selection: str = "ATM", expiry_selection: str = "NEAREST",
                strike: Decimal | None = None, expiry: date | None = None,
                order_type: str = "MARKET", limit_offset_pct: Decimal = D("0"),
                bot_id: uuid.UUID | None = None, signal_id: uuid.UUID | None = None,
                bot_risk_per_trade: Decimal | None = None,
                bot_square_off: str | None = None) -> Order:
    existing = db.scalar(select(Order).where(Order.client_order_id == client_order_id))
    if existing is not None:
        return existing  # idempotent
    rs = RISK.lock_user(db, user_id)
    c = resolve_contract(cache, underlying=underlying, option_type=option_type,
                         strike_selection=strike_selection, expiry_selection=expiry_selection,
                         strike=strike, expiry=expiry)
    ask = D(str(c["ask"])) if c.get("ask") else None
    quote_ok = c["quality"] != "FAIL" and ask is not None and bool(c.get("bid"))
    entry_ref = ask or D(str(c.get("ltp") or "0"))
    tick = c["tick_size"]
    stop_price = _level(entry_ref, stop, True, tick) if entry_ref > 0 else None
    target_price = _level(entry_ref, target, False, tick) if entry_ref > 0 else None
    day = RISK.trading_day(now)
    brokerage = settings.paper_brokerage_per_order
    lot = c["lot_size"]
    if lots is None:
        budget = min(rs.max_risk_per_trade, bot_risk_per_trade or rs.max_risk_per_trade)
        per_lot = ((entry_ref - (stop_price or entry_ref)) * lot + D(CH.round_trip(
            buy=entry_ref, sell=stop_price or entry_ref, qty=lot, day=day,
            brokerage=brokerage)["total"]["total"])) if entry_ref > 0 else D("0")
        lots = int(budget / per_lot) if per_lot > 0 else 1
        lots = max(1, min(lots, rs.max_lots_per_trade))
    qty = lots * lot
    rt = CH.round_trip(buy=entry_ref, sell=stop_price or entry_ref, qty=qty, day=day,
                       brokerage=brokerage) if entry_ref > 0 else None
    buy_ch = D(rt["buy"]["total"]) if rt else D("0")
    decision = RISK.pretrade(
        db, settings, rs, user_id=user_id, now=now, lots=lots, lot_size=lot,
        entry_price=entry_ref, stop_price=stop_price or entry_ref,
        round_trip_charges=D(rt["total"]["total"]) if rt else D("0"), buy_charges=buy_ch,
        quote_ok=quote_ok and entry_ref > 0,
        quote_detail="No fresh two-sided quote for this contract.", expiry=c["expiry"],
        bot_id=bot_id, bot_risk_per_trade=bot_risk_per_trade, bot_square_off=bot_square_off)
    limit_price = None
    if order_type == "LIMIT" and ask is not None:
        limit_price = (ask * (1 + D(limit_offset_pct) / 100) / tick).to_integral_value(
            rounding=ROUND_FLOOR) * tick
    o = Order(
        user_id=user_id, bot_id=bot_id, signal_id=signal_id, client_order_id=client_order_id,
        broker="PAPER", mode="PAPER", purpose="ENTRY", side="BUY", underlying=underlying,
        trading_symbol=c["trading_symbol"], security_id=c["security_id"], expiry=c["expiry"],
        strike=c["strike"], option_type=option_type, lot_size=lot,
        lot_size_source=c["lot_size_source"], quantity=qty, order_type=order_type,
        limit_price=limit_price, stop_price=stop_price, target_price=target_price,
        trailing_pct=D(str(trailing["value"])) if trailing and trailing["type"] == "PERCENT"
        else None, data_source=c["source"],
        risk_snapshot={**decision.snapshot, "checks": decision.checks,
                       "charges_estimate": rt, "tick_size": str(tick)},
        created_at=now, updated_at=now)
    db.add(o)
    db.flush()
    db.add(OrderEvent(order_id=o.id, ts=now, from_status=None, to_status="CREATED",
                      message=f"{'Bot' if bot_id else 'Manual'} entry: BUY {qty} "
                              f"{c['trading_symbol']} ({lots} lot)."))
    if not decision.allowed:
        o.reject_reason = " ".join(decision.reasons)[:500]
        move(db, o, "REJECTED", "Risk manager: " + o.reject_reason)
        return o
    move(db, o, "VALIDATED", "All pre-trade risk checks passed.")
    _submit(db, broker_for(db, settings, user_id, cache), o, _contract_dict(c))
    return o


def _submit(db: Session, broker: PaperBroker, o: Order, contract: dict[str, Any]) -> None:
    move(db, o, "SUBMITTED", "Sent to paper broker.")
    try:
        st = broker.place_order(OrderRequest(
            client_order_id=o.client_order_id, side=o.side,  # type: ignore[arg-type]
            quantity=o.quantity, order_type=o.order_type,  # type: ignore[arg-type]
            limit_price=o.limit_price, contract=contract))
    except BrokerError as exc:
        o.reject_reason = str(exc)[:500]
        move(db, o, "REJECTED", f"Broker rejected: {exc}")
        return
    except BrokerUnavailable as exc:
        move(db, o, "UNKNOWN", f"No confirmation from broker ({exc}); will reconcile, not resend.")
        return
    o.broker_order_id = st.broker_order_id
    move(db, o, "ACKNOWLEDGED", f"Broker order {st.broker_order_id}.")


# --------------------------------------------------------------------- exits


def place_exit(db: Session, settings: Settings, cache: ChainCache, pos: Position, reason: str,
               now: datetime) -> Order | None:
    active = db.scalar(select(Order).where(Order.position_id == pos.id, Order.purpose == "EXIT",
                                           Order.status.in_(ACTIVE_ORDER_STATES)))
    if active is not None or pos.quantity <= 0:
        return active
    n = db.scalar(select(func.count()).select_from(Order).where(
        Order.position_id == pos.id, Order.purpose == "EXIT")) or 0
    entry = db.get(Order, pos.entry_order_id)
    o = Order(
        user_id=pos.user_id, bot_id=pos.bot_id, position_id=pos.id,
        client_order_id=f"EXIT-{pos.id.hex[:16]}-{n + 1}", broker="PAPER", mode="PAPER",
        purpose="EXIT", side="SELL", underlying=pos.underlying,
        trading_symbol=pos.trading_symbol, security_id=pos.security_id, expiry=pos.expiry,
        strike=pos.strike, option_type=pos.option_type, lot_size=pos.lot_size,
        lot_size_source=entry.lot_size_source if entry else "UNKNOWN", quantity=pos.quantity,
        order_type="MARKET", exit_reason=reason, data_source=pos.data_source,
        risk_snapshot={"reason": reason}, created_at=now, updated_at=now)
    db.add(o)
    db.flush()
    db.add(OrderEvent(order_id=o.id, ts=now, from_status=None, to_status="CREATED",
                      message=f"Exit ({reason}): SELL {pos.quantity} {pos.trading_symbol}."))
    move(db, o, "VALIDATED", "Exits are always allowed by the risk manager.")
    pos.status = "CLOSING"
    tick = D(str((entry.risk_snapshot or {}).get("tick_size", "0.05"))) if entry else D("0.05")
    _submit(db, broker_for(db, settings, pos.user_id, cache), o, {
        "underlying": pos.underlying, "expiry": pos.expiry, "strike": pos.strike,
        "option_type": pos.option_type, "security_id": pos.security_id, "tick_size": tick})
    return o


# ---------------------------------------------------------------- fill sync


def _apply_fill(db: Session, settings: Settings, o: Order, fill_id: str, qty: int,
                price: Decimal, ts: datetime, first: bool) -> None:
    ch = CH.compute(side=o.side, price=price, qty=qty, day=RISK.trading_day(ts),
                    brokerage=settings.paper_brokerage_per_order, charge_brokerage=first)
    ds = RISK.daily_state(db, o.user_id, RISK.trading_day(ts))
    ds.charges += ch.total
    if o.purpose == "ENTRY":
        pos = db.scalar(select(Position).where(Position.entry_order_id == o.id))
        if pos is None:
            pos = Position(
                user_id=o.user_id, bot_id=o.bot_id, entry_order_id=o.id, mode=o.mode,
                underlying=o.underlying, trading_symbol=o.trading_symbol,
                security_id=o.security_id, expiry=o.expiry, strike=o.strike,
                option_type=o.option_type, lot_size=o.lot_size, quantity=0, entry_qty=0,
                avg_entry=price, stop_price=o.stop_price, target_price=o.target_price,
                trailing_pct=o.trailing_pct, high_water=price, last_price=price,
                last_price_at=ts, data_source=o.data_source, opened_at=ts, status="OPEN")
            db.add(pos)
            ds.entries += 1
            db.flush()
        total = pos.avg_entry * pos.entry_qty + price * qty
        pos.entry_qty += qty
        pos.quantity += qty
        pos.avg_entry = (total / pos.entry_qty).quantize(D("0.0001"))
        pos.charges_total += ch.total
        o.position_id = pos.id
    else:
        pos = db.get(Position, o.position_id)
        gross = (price - pos.avg_entry) * qty
        prev = (pos.avg_exit or D("0")) * pos.exit_qty
        pos.exit_qty += qty
        pos.avg_exit = ((prev + price * qty) / pos.exit_qty).quantize(D("0.0001"))
        pos.quantity -= qty
        pos.realized_gross += gross
        pos.charges_total += ch.total
        ds.realized_gross += gross
        if pos.quantity == 0:
            pos.status, pos.closed_at, pos.exit_reason = "CLOSED", ts, o.exit_reason
            if o.exit_reason == "STOP_LOSS":
                ds.stop_loss_exits += 1
    db.add(Fill(order_id=o.id, user_id=o.user_id, broker=o.broker,
                broker_fill_id=fill_id,
                side=o.side, quantity=qty, price=price, ts=ts, charges=ch.as_dict(),
                charges_total=ch.total))


def sync_order(db: Session, settings: Settings, broker: PaperBroker, o: Order) -> None:
    if not o.broker_order_id:
        return
    st = broker.get_order(o.broker_order_id)
    _sync_from_state(db, settings, o, st)


def _sync_from_state(db: Session, settings: Settings, o: Order, st: BrokerOrderState) -> None:
    """Apply new broker fills (by trade id) and the broker's order status."""
    known = set(db.scalars(select(Fill.broker_fill_id).where(Fill.order_id == o.id)).all())
    new = 0
    for f in st.fills:
        if f.trade_id in known:
            continue
        _apply_fill(db, settings, o, f.trade_id, f.quantity, f.price, f.ts,
                    first=o.filled_qty == 0)
        o.filled_qty += f.quantity
        new += 1
    if new:
        o.avg_fill_price = st.avg_price
    target = {"FILLED": "FILLED", "PARTIAL": "PARTIALLY_FILLED", "CANCELLED": "CANCELLED",
              "REJECTED": "REJECTED"}.get(st.status)
    if target and (target != o.status or (target == "PARTIALLY_FILLED" and new)):
        if target == "REJECTED":
            o.reject_reason = st.reject_reason
        px = f"{o.avg_fill_price:.2f}" if o.avg_fill_price is not None else "—"
        msg = {"FILLED": f"Filled {o.filled_qty} @ {px}.",
               "PARTIALLY_FILLED": f"Partially filled {o.filled_qty}/{o.quantity} @ {px}.",
               "CANCELLED": f"Cancelled with {o.filled_qty}/{o.quantity} filled."
               + (f" {st.reject_reason}" if st.reject_reason else ""),
               "REJECTED": f"Rejected: {st.reject_reason}"}[target]
        move(db, o, target, msg)
    if o.purpose == "EXIT" and o.status in ("REJECTED", "CANCELLED") and o.position_id:
        pos = db.get(Position, o.position_id)
        if pos and pos.quantity > 0:
            pos.status = "OPEN"  # exit failed; the next pass will try again


def cancel(db: Session, settings: Settings, cache: ChainCache, o: Order, reason: str) -> None:
    if o.status not in ("ACKNOWLEDGED", "PARTIALLY_FILLED", "SUBMITTED"):
        return
    move(db, o, "CANCEL_PENDING", reason)
    broker = broker_for(db, settings, o.user_id, cache)
    st = broker.cancel_order(o.broker_order_id)  # type: ignore[arg-type]
    _sync_from_state(db, settings, o, st)


# ------------------------------------------------------------ reconciliation


def reconcile(db: Session, settings: Settings, cache: ChainCache, user_id: uuid.UUID,
              now: datetime) -> None:
    """Resolve orders whose outcome is unknown by asking the broker. Never resend."""
    broker = broker_for(db, settings, user_id, cache)
    for o in db.scalars(select(Order).where(
            Order.user_id == user_id,
            Order.status.in_(("UNKNOWN", "RECONCILIATION_REQUIRED")))).all():
        st = broker.find_by_client_id(o.client_order_id)
        if st is None:
            if o.status == "UNKNOWN" and now - (o.submitted_at or o.created_at) > UNKNOWN_GRACE:
                move(db, o, "RECONCILIATION_REQUIRED",
                     "Broker has no record of this order. It was not resent; review needed.")
            continue
        o.broker_order_id = st.broker_order_id
        move(db, o, "ACKNOWLEDGED", f"Reconciled with broker order {st.broker_order_id}.")
        _sync_from_state(db, settings, o, st)


# ------------------------------------------------------------ position risk


def _hhmm(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def demo_24x7(settings: Settings) -> bool:
    """DEMO data is a synthetic market that never closes; session rules are relaxed."""
    return settings.market_data_mode == MarketDataMode.DEMO and settings.demo_market_always_open


def manage_positions(db: Session, settings: Settings, cache: ChainCache, rs: RiskSettings,
                     user_id: uuid.UUID, now: datetime) -> None:
    local = now.astimezone(IST)
    ds = RISK.daily_state(db, user_id, RISK.trading_day(now))
    positions = RISK.open_positions(db, user_id)
    for p in positions:
        q = cache.quote({"underlying": p.underlying, "expiry": p.expiry, "strike": p.strike,
                         "option_type": p.option_type})
        if q and (q.get("bid") or q.get("ltp")):
            mark = D(str(q.get("bid") or q["ltp"]))
            p.last_price, p.last_price_at = mark, now
            if p.high_water is None or mark > p.high_water:
                p.high_water = mark
                if p.trailing_pct:
                    tick = D("0.05")
                    trail = (mark * (1 - p.trailing_pct / 100) / tick).to_integral_value(
                        rounding=ROUND_FLOOR) * tick
                    if p.stop_price is None or trail > p.stop_price:
                        p.stop_price = trail
    day_net = ds.realized_net + RISK.unrealized(positions)
    if ds.loss_limit_hit_at is None and day_net <= -rs.daily_loss_limit:
        ds.loss_limit_hit_at = now
        _cancel_pending_entries(db, settings, cache, user_id, "Daily loss stop reached.")
    if ds.profit_lock_hit_at is None and ds.realized_net >= rs.daily_profit_lock:
        ds.profit_lock_hit_at = now
        _cancel_pending_entries(db, settings, cache, user_id, "Daily profit lock reached.")
    if rs.kill_switch_engaged:
        _cancel_pending_entries(db, settings, cache, user_id, "Kill switch engaged.")
    for p in positions:
        if p.status != "OPEN":
            continue
        square_off = _hhmm(rs.square_off_time_ist)
        reason = None
        if rs.kill_switch_engaged and rs.square_off_on_kill_switch:
            reason = "KILL_SWITCH"
        elif ds.loss_limit_hit_at is not None and rs.exit_on_loss_limit:
            reason = "LOSS_LIMIT"
        elif (local.time() >= square_off and not demo_24x7(settings)) \
                or (p.expiry <= local.date() and local.time() >= square_off):
            reason = "SQUARE_OFF"
        elif p.last_price is not None and p.stop_price is not None and p.last_price <= p.stop_price:
            reason = "STOP_LOSS"
        elif p.last_price is not None and p.target_price is not None and \
                p.last_price >= p.target_price:
            reason = "TARGET"
        if reason:
            place_exit(db, settings, cache, p, reason, now)


def _cancel_pending_entries(db: Session, settings: Settings, cache: ChainCache,
                            user_id: uuid.UUID, reason: str) -> None:
    for o in RISK.pending_entries(db, user_id):
        cancel(db, settings, cache, o, reason)


def expire_stale_entries(db: Session, settings: Settings, cache: ChainCache,
                         user_id: uuid.UUID, now: datetime) -> None:
    for o in RISK.pending_entries(db, user_id):
        if o.order_type == "LIMIT" and now - o.created_at > ENTRY_LIMIT_TTL:
            cancel(db, settings, cache, o, "Entry limit order not filled within 5 minutes.")


# ------------------------------------------------------------------ the pass


def users_with_activity(db: Session) -> list[uuid.UUID]:
    a = db.scalars(select(Order.user_id).where(Order.status.in_(ACTIVE_ORDER_STATES))).all()
    b = db.scalars(select(Position.user_id).where(Position.status != "CLOSED")).all()
    return sorted(set(a) | set(b), key=str)


def process_user(db: Session, settings: Settings, cache: ChainCache, user_id: uuid.UUID,
                 now: datetime) -> None:
    rs = RISK.lock_user(db, user_id)
    broker = broker_for(db, settings, user_id, cache)
    reconcile(db, settings, cache, user_id, now)
    broker.match(now)
    for o in db.scalars(select(Order).where(
            Order.user_id == user_id,
            Order.status.in_(("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED",
                              "CANCEL_PENDING")))).all():
        sync_order(db, settings, broker, o)
    expire_stale_entries(db, settings, cache, user_id, now)
    manage_positions(db, settings, cache, rs, user_id, now)
    # Exits placed above fill on this pass too.
    broker.match(now)
    for o in db.scalars(select(Order).where(
            Order.user_id == user_id, Order.purpose == "EXIT",
            Order.status.in_(("ACKNOWLEDGED", "PARTIALLY_FILLED")))).all():
        sync_order(db, settings, broker, o)


def run_pass(db: Session, settings: Settings, provider: MarketDataProvider,
             now: datetime) -> dict[str, int]:
    cache = ChainCache(provider, settings)
    stats = {"users": 0, "errors": 0}
    for uid in users_with_activity(db):
        try:
            process_user(db, settings, cache, uid, now)
            db.commit()
            stats["users"] += 1
        except Exception:
            db.rollback()
            stats["errors"] += 1
            logger.exception("execution_pass_user_failed", extra={"user_id": str(uid)})
    return stats


def fill_now(db: Session, settings: Settings, cache: ChainCache, o: Order,
             now: datetime) -> None:
    """Match immediately after placing (paper fills are near-instant)."""
    if o.status not in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
        return
    broker = broker_for(db, settings, o.user_id, cache)
    broker.match(now)
    sync_order(db, settings, broker, o)

