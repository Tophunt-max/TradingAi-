"""Position-sizing proposal with the execution layer's own math.

Per-lot risk = (entry - stop) x lot size + round-trip charges (buy at entry, sell at the
stop) from app.execution.charges, exactly as `execution.service.place_entry` computes it
when no lot count is given. The stop is the premium-% level floored to the tick like an
order's stop. The proposal is capped by the user's max lots per trade and the manual
order endpoint's 10-lot limit. Unlike place_entry (which falls back to 1 lot and lets the
risk manager reject), the proposal is 0 lots when one lot does not fit the risk budget.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.execution import charges as CH
from app.execution.service import _level

D = Decimal
MANUAL_MAX_LOTS = 10  # ManualEntry.lots upper bound (POST /orders/paper)


def propose(*, entry_premium: Decimal, stop_pct: Decimal, target_pct: Decimal, lot_size: int,
            tick: Decimal, max_risk_per_trade: Decimal, max_lots: int, brokerage: Decimal,
            day: date, reduce_exposure: bool = False) -> dict[str, Any]:
    stop = _level(entry_premium, {"type": "PERCENT", "value": str(stop_pct)}, True, tick)
    target = _level(entry_premium, {"type": "PERCENT", "value": str(target_pct)}, False, tick)
    assert stop is not None and target is not None
    rt_lot = CH.round_trip(buy=entry_premium, sell=stop, qty=lot_size, day=day,
                           brokerage=brokerage)
    per_lot = (entry_premium - stop) * lot_size + D(rt_lot["total"]["total"])
    budget = max_risk_per_trade
    by_budget = int(budget / per_lot) if per_lot > 0 else 0
    cap = min(max_lots, MANUAL_MAX_LOTS)
    notes = []
    if reduce_exposure:
        cap = min(cap, 1)
        notes.append("Event-risk gate asks to reduce exposure: capped at 1 lot.")
    lots = max(0, min(by_budget, cap))
    if by_budget < 1:
        notes.append(f"One lot risks ₹{per_lot:.2f} (stop distance + charges), above the "
                     f"₹{budget:.2f} risk-per-trade budget.")
    elif by_budget > cap:
        notes.append(f"Risk budget allows {by_budget} lot(s); capped at {cap}.")
    qty = lots * lot_size
    rt = CH.round_trip(buy=entry_premium, sell=stop, qty=qty, day=day, brokerage=brokerage) \
        if qty else None
    reward = (target - entry_premium) * lot_size
    risk_unit = entry_premium - stop
    return {
        "lots": lots, "lot_size": lot_size, "quantity": qty,
        "entry_premium": str(entry_premium), "stop_premium": str(stop),
        "target_premium": str(target), "stop_pct": str(stop_pct), "target_pct": str(target_pct),
        "risk_per_lot": str(per_lot.quantize(D("0.01"))),
        "planned_risk": str(((entry_premium - stop) * qty + D(rt["total"]["total"]))
                            .quantize(D("0.01"))) if rt else "0.00",
        "round_trip_charges": rt["total"]["total"] if rt else "0.00",
        "buy_charges": rt["buy"]["total"] if rt else "0.00",
        "risk_budget": str(budget), "lots_by_budget": by_budget, "lots_cap": cap,
        "premium_risk_reward": round(float((target - entry_premium) / risk_unit), 2)
        if risk_unit > 0 else None,
        "reward_per_lot_before_costs": str(reward.quantize(D("0.01"))),
        "method": "Same formula as execution.place_entry: (entry − stop) × lot + round-trip "
                  "charges (CH.round_trip) per lot; floor(budget / per-lot risk), capped by "
                  "max lots per trade.",
        "notes": notes,
    }
