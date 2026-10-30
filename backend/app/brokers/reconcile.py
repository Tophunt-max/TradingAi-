"""REAL-account reconciliation with the broker.

- `reconcile_unknown_orders`: orders whose placement outcome is unknown (UNKNOWN /
  RECONCILIATION_REQUIRED) are looked up at the broker by our client_order_id (the tag the
  client sent with the order). They are never resent. Broker-confirmed fills are applied
  through the normal fill path, so REAL P&L only ever comes from broker fills.
- `reconcile_charges`: the broker's charges accrued for a day vs the sum of our per-fill
  estimates for that day. The difference is stored, not hidden.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerUnavailable
from app.brokers.client import BrokerClient, BrokerFunds
from app.brokers.real import RealBrokerExecutionAdapter
from app.core.config import Settings
from app.core.time import IST, utcnow
from app.models.accounts import Account
from app.models.brokers import BrokerChargeReconciliation
from app.models.execution import Fill, Order

D = Decimal
COMPONENTS = ("brokerage", "stt", "exchange_txn", "sebi", "stamp_duty", "gst")


def reconcile_unknown_orders(db: Session, settings: Settings, account: Account,
                             client: BrokerClient | None = None,
                             now: datetime | None = None) -> dict[str, Any]:
    from app.execution import service as EX
    from app.execution.state import move

    if account.mode != "REAL":
        raise ValueError("Broker reconciliation applies to REAL accounts only.")
    now = now or utcnow()
    if client is None:
        from app.brokers.connections import real_broker_client

        client = real_broker_client(db, settings, account)
    adapter = RealBrokerExecutionAdapter(db, settings, account, client)
    out = {"checked": 0, "resolved": 0, "not_found": 0, "unavailable": 0}
    for o in db.scalars(select(Order).where(
            Order.account_id == account.id,
            Order.status.in_(("UNKNOWN", "RECONCILIATION_REQUIRED")))).all():
        out["checked"] += 1
        try:
            st = adapter.find_by_client_id(o.client_order_id)
        except BrokerUnavailable:
            out["unavailable"] += 1
            continue
        if st is None:
            out["not_found"] += 1
            if o.status == "UNKNOWN" and now - (o.submitted_at or o.created_at) > \
                    EX.UNKNOWN_GRACE:
                move(db, o, "RECONCILIATION_REQUIRED",
                     "Broker has no record of this order. It was not resent; review needed.")
            continue
        o.broker_order_id = st.broker_order_id
        move(db, o, "ACKNOWLEDGED", f"Reconciled with broker order {st.broker_order_id}.")
        EX._sync_from_state(db, settings, o, st)
        out["resolved"] += 1
    db.flush()
    return out


def estimated_charges(db: Session, account: Account, day: date) -> tuple[Decimal, int,
                                                                         dict[str, str]]:
    start = datetime.combine(day, time(0), IST)
    fills = db.scalars(select(Fill).where(Fill.account_id == account.id, Fill.ts >= start,
                                          Fill.ts < start + timedelta(days=1))).all()
    parts = {k: D("0") for k in COMPONENTS}
    for f in fills:
        for k in COMPONENTS:
            parts[k] += D(str(f.charges.get(k, "0")))
    return sum((f.charges_total for f in fills), D("0")), len(fills), \
        {k: str(v) for k, v in parts.items()}


def reconcile_charges(db: Session, account: Account, day: date, funds: BrokerFunds,
                      broker: str) -> BrokerChargeReconciliation | None:
    """Store broker-reported (brokerage + segment charges accrued today) vs our estimate.
    Returns None when the broker reported neither figure."""
    if account.mode != "REAL":
        raise ValueError("Charges reconciliation applies to REAL accounts only.")
    if funds.brokerage_today is None and funds.charges_today is None:
        return None
    reported = (funds.brokerage_today or D("0")) + (funds.charges_today or D("0"))
    est, n, parts = estimated_charges(db, account, day)
    row = db.scalar(select(BrokerChargeReconciliation).where(
        BrokerChargeReconciliation.account_id == account.id,
        BrokerChargeReconciliation.trading_day == day))
    if row is None:
        row = BrokerChargeReconciliation(account_id=account.id, account_mode="REAL",
                                         broker=broker, trading_day=day)
        db.add(row)
    row.broker_reported, row.estimated, row.diff, row.fills = reported, est, reported - est, n
    row.detail = {"broker": {"brokerage": str(funds.brokerage_today),
                             "segment_charges": str(funds.charges_today)},
                  "estimated_breakdown": parts,
                  "note": "Broker figures are accrued for the day (GET /funds). Positive diff: "
                          "the broker charged more than estimated."}
    row.created_at = utcnow()
    db.flush()
    return row
