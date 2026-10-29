"""Persistent order state machine.

CREATED -> VALIDATED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED
plus REJECTED, CANCEL_PENDING, CANCELLED, UNKNOWN and RECONCILIATION_REQUIRED.
Every transition is checked and written to order_events.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.execution import TERMINAL_ORDER_STATES, Order, OrderEvent

ALLOWED: dict[str, set[str]] = {
    "CREATED": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"SUBMITTED", "REJECTED"},
    "SUBMITTED": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "UNKNOWN",
                  "CANCELLED"},
    "ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "CANCELLED", "REJECTED",
                     "UNKNOWN"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "CANCELLED",
                         "UNKNOWN"},
    "CANCEL_PENDING": {"CANCELLED", "FILLED", "PARTIALLY_FILLED", "UNKNOWN"},
    "UNKNOWN": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED",
                "RECONCILIATION_REQUIRED"},
    "RECONCILIATION_REQUIRED": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELLED",
                                "REJECTED"},
    "FILLED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}


class IllegalTransition(RuntimeError):
    pass


def move(db: Session, order: Order, to: str, message: str) -> None:
    frm = order.status
    if to == frm and to != "PARTIALLY_FILLED":
        return
    if to not in ALLOWED[frm]:
        raise IllegalTransition(f"Order {order.client_order_id}: {frm} -> {to} is not allowed.")
    order.status = to
    now = utcnow()
    order.updated_at = now
    if to == "SUBMITTED":
        order.submitted_at = now
    if to in TERMINAL_ORDER_STATES:
        order.terminal_at = now
    db.add(OrderEvent(order_id=order.id, ts=now, from_status=frm, to_status=to,
                      message=message[:500]))
