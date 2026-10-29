"""Versioned charge schedules for NSE index options (buying and selling premium).

Statutory rates change by regulation, so each schedule has an effective date
and the schedule in force on the trade date is used. Sources (checked Oct 2026):
- STT on options premium (sell side): 0.1% from 1 Oct 2024; 0.15% from
  1 Apr 2026 (Union Budget 2026-27).
- NSE options transaction charge: 0.03503% of premium (from 1 Oct 2024).
- SEBI turnover fee: Rs 10 per crore (0.0001%).
- Stamp duty: 0.003% of premium, buy side only.
- GST: 18% on brokerage + exchange transaction charge + SEBI fee.
Brokerage is broker-specific: the INDstocks API advertises a flat Rs 10 per
executed order (the INDmoney app lists Rs 20), so it is configurable.

Not modelled: IPFT, STT on exercise of in-the-money options at expiry (the
platform squares off before expiry), and exchange rounding. Estimates must be
reconciled against broker contract notes (Phase 8).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

D = Decimal
CENT = D("0.01")


@dataclass(frozen=True)
class StatutorySchedule:
    id: str
    effective_from: date
    stt_sell_pct: Decimal
    exchange_txn_pct: Decimal
    sebi_pct: Decimal
    stamp_buy_pct: Decimal
    gst_pct: Decimal
    source: str


SCHEDULES = (
    StatutorySchedule("nse-opt-2024-10-01", date(2024, 10, 1), D("0.1"), D("0.03503"),
                      D("0.0001"), D("0.003"), D("18"),
                      "STT 0.1% (Finance Act 2024); NSE circular Oct 2024."),
    StatutorySchedule("nse-opt-2026-04-01", date(2026, 4, 1), D("0.15"), D("0.03503"),
                      D("0.0001"), D("0.003"), D("18"),
                      "STT 0.15% from 1 Apr 2026 (Union Budget 2026-27)."),
)

BROKERAGE_PER_ORDER = {"INDSTOCKS_API": D("10"), "INDMONEY_APP": D("20")}


def schedule_for(day: date) -> StatutorySchedule:
    valid = [s for s in SCHEDULES if s.effective_from <= day]
    if not valid:
        raise ValueError(f"No charge schedule in force on {day}.")
    return max(valid, key=lambda s: s.effective_from)


@dataclass(frozen=True)
class Charges:
    schedule_id: str
    brokerage: Decimal
    stt: Decimal
    exchange_txn: Decimal
    sebi: Decimal
    stamp_duty: Decimal
    gst: Decimal

    @property
    def total(self) -> Decimal:
        return (self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp_duty
                + self.gst)

    def as_dict(self) -> dict[str, str]:
        d = {k: str(v) for k, v in asdict(self).items()}
        d["total"] = str(self.total)
        return d

    def __add__(self, other: Charges) -> Charges:
        return Charges(f"{self.schedule_id}+{other.schedule_id}" if self.schedule_id !=
                       other.schedule_id else self.schedule_id,
                       self.brokerage + other.brokerage, self.stt + other.stt,
                       self.exchange_txn + other.exchange_txn, self.sebi + other.sebi,
                       self.stamp_duty + other.stamp_duty, self.gst + other.gst)


def _r(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def compute(*, side: str, price: Decimal, qty: int, day: date, brokerage: Decimal,
            charge_brokerage: bool = True) -> Charges:
    """Charges for one fill. Brokerage is charged once per order (first fill)."""
    s = schedule_for(day)
    turnover = D(price) * qty
    brk = brokerage if charge_brokerage else D("0")
    stt = turnover * s.stt_sell_pct / 100 if side == "SELL" else D("0")
    txn = turnover * s.exchange_txn_pct / 100
    sebi = turnover * s.sebi_pct / 100
    stamp = turnover * s.stamp_buy_pct / 100 if side == "BUY" else D("0")
    gst = (brk + txn + sebi) * s.gst_pct / 100
    return Charges(s.id, _r(brk), _r(stt), _r(txn), _r(sebi), _r(stamp), _r(gst))


def round_trip(*, buy: Decimal, sell: Decimal, qty: int, day: date,
               brokerage: Decimal) -> dict[str, object]:
    """Estimate for a full buy-then-sell trade, with net P&L and break-even."""
    b = compute(side="BUY", price=buy, qty=qty, day=day, brokerage=brokerage)
    s = compute(side="SELL", price=sell, qty=qty, day=day, brokerage=brokerage)
    total = b + s
    gross = (D(sell) - D(buy)) * qty
    # Break-even sell price: gross profit just covers charges (solve approximately:
    # sell-side charges scale with the sell price, so iterate twice).
    be = D(buy) + total.total / qty if qty else D(buy)
    for _ in range(3):
        s2 = compute(side="SELL", price=be, qty=qty, day=day, brokerage=brokerage)
        be = D(buy) + (b.total + s2.total) / qty
    return {"buy": b.as_dict(), "sell": s.as_dict(), "total": total.as_dict(),
            "gross_pnl": str(_r(gross)), "net_pnl": str(_r(gross - total.total)),
            "break_even_price": str(be.quantize(D("0.0001"))), "schedule": b.schedule_id}
