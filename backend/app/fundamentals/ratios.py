"""Pure fundamental-ratio functions. No I/O, no guessing.

A period is a plain dict: {"period_end": date, "period_type": "Q"|"FY",
"value_basis": str, "items": {line_item: Decimal | None}, ...}. Any ratio whose
inputs are missing (or whose denominator is zero) is None. Nothing is imputed:
EV/EBITDA, for example, needs a depreciation & amortisation line and is None
without one.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

D = Decimal
FLOW_ITEMS = ("revenue", "gross_profit", "operating_income", "net_income", "eps",
              "operating_cash_flow", "capex", "depreciation_amortization", "interest_expense",
              "dividends_per_share")
ACCRUALS_LOW_QUALITY = 0.10  # (net income − OCF) / total assets above this → LOW quality


def _v(p: dict[str, Any] | None, k: str) -> Decimal | None:
    if p is None:
        return None
    v = p["items"].get(k)
    return None if v is None else D(str(v))


def div(a: Decimal | None, b: Decimal | None, digits: int = 4) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round(float(a / b), digits)


def growth(cur: Decimal | None, prev: Decimal | None) -> float | None:
    """(cur − prev) / |prev|. None when prev is missing or zero."""
    if cur is None or prev is None or prev == 0:
        return None
    return round(float((cur - prev) / abs(prev)), 4)


def sub(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a - b


def _quarters(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted([p for p in periods if p["period_type"] == "Q"], key=lambda p: p["period_end"])


def find_period(periods: list[dict[str, Any]], period_type: str, end: date,
                tolerance_days: int = 20) -> dict[str, Any] | None:
    """The period of this type whose end is within `tolerance_days` of `end`."""
    best = None
    for p in periods:
        if p["period_type"] != period_type:
            continue
        gap = abs((p["period_end"] - end).days)
        if gap <= tolerance_days and (best is None or gap < best[0]):
            best = (gap, p)
    return best[1] if best else None


def previous_quarter(periods: list[dict[str, Any]], cur: dict[str, Any]) -> dict | None:
    return find_period(periods, "Q", cur["period_end"] - timedelta(days=91))


def year_ago(periods: list[dict[str, Any]], cur: dict[str, Any]) -> dict | None:
    return find_period(periods, cur["period_type"], cur["period_end"] - timedelta(days=365))


def ttm(periods: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Trailing twelve months from the last four consecutive quarters.

    Flow items are summed (only if all four quarters have the item); balance-sheet
    items come from the latest quarter. None if four consecutive quarters are not
    available.
    """
    qs = _quarters(periods)
    if len(qs) < 4:
        return None
    last4 = qs[-4:]
    for a, b in zip(last4[:-1], last4[1:], strict=True):
        if not 70 <= (b["period_end"] - a["period_end"]).days <= 110:
            return None
    items: dict[str, Decimal | None] = dict(last4[-1]["items"])
    for k in FLOW_ITEMS:
        vals = [_v(q, k) for q in last4]
        items[k] = None if any(v is None for v in vals) else sum(vals, D(0))  # type: ignore[arg-type]
    return {"period_end": last4[-1]["period_end"], "period_type": "TTM",
            "value_basis": "TTM", "items": items,
            "basis": f"Sum of 4 quarters ending {last4[-1]['period_end'].isoformat()}"}


def ebitda(p: dict[str, Any] | None) -> Decimal | None:
    oi, da = _v(p, "operating_income"), _v(p, "depreciation_amortization")
    return None if oi is None or da is None else oi + da


def period_ratios(cur: dict[str, Any], periods: list[dict[str, Any]]) -> dict[str, Any]:
    """Ratios of one period. `periods` supplies the comparison periods."""
    prev_q = previous_quarter(periods, cur) if cur["period_type"] == "Q" else None
    prev_y = year_ago(periods, cur)
    rev, ni, ocf = _v(cur, "revenue"), _v(cur, "net_income"), _v(cur, "operating_cash_flow")
    capex = _v(cur, "capex")
    fcf = sub(ocf, capex)
    accruals = div(sub(ni, ocf), _v(cur, "total_assets"))
    return {
        "period_end": cur["period_end"].isoformat(), "period_type": cur["period_type"],
        "value_basis": cur["value_basis"],
        "revenue_growth_yoy": growth(rev, _v(prev_y, "revenue")),
        "revenue_growth_qoq": growth(rev, _v(prev_q, "revenue")),
        "eps_growth_yoy": growth(_v(cur, "eps"), _v(prev_y, "eps")),
        "gross_margin": div(_v(cur, "gross_profit"), rev),
        "operating_margin": div(_v(cur, "operating_income"), rev),
        "net_margin": div(ni, rev),
        "operating_cash_flow": None if ocf is None else str(ocf),
        "free_cash_flow": None if fcf is None else str(fcf),
        "debt_to_equity": div(_v(cur, "total_debt"), _v(cur, "equity")),
        "interest_coverage": div(_v(cur, "operating_income"), _v(cur, "interest_expense")),
        "working_capital": None if sub(_v(cur, "current_assets"),
                                       _v(cur, "current_liabilities")) is None
        else str(sub(_v(cur, "current_assets"), _v(cur, "current_liabilities"))),
        "current_ratio": div(_v(cur, "current_assets"), _v(cur, "current_liabilities")),
        "cash_conversion": div(ocf, ni) if ni is not None and ni > 0 else None,
        "accruals_ratio": accruals,
        "earnings_quality": None if accruals is None else
        ("LOW" if accruals > ACCRUALS_LOW_QUALITY else "OK"),
    }


def return_ratios(base: dict[str, Any] | None) -> dict[str, float | None]:
    """ROE and ROCE on a twelve-month base (TTM or FY). None for a single quarter."""
    if base is None:
        return {"roe": None, "roce": None}
    return {"roe": div(_v(base, "net_income"), _v(base, "equity")),
            "roce": div(_v(base, "operating_income"), _v(base, "capital_employed"))}


def valuation(price: Decimal | None, base: dict[str, Any] | None,
              estimates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Price-based multiples on a twelve-month base (TTM preferred, else FY)."""
    out: dict[str, Any] = {"pe": None, "pb": None, "ps": None, "ev_ebitda": None,
                           "dividend_yield": None, "forward_pe": None, "notes": []}
    if price is None or base is None:
        out["notes"].append("Needs a price and a twelve-month base (4 quarters or a FY).")
        return out
    shares = _v(base, "shares_outstanding")
    eps, equity = _v(base, "eps"), _v(base, "equity")
    if eps is not None and eps > 0:
        out["pe"] = div(price, eps, 2)
    elif eps is not None:
        out["notes"].append("P/E not meaningful: trailing EPS is zero or negative.")
    if shares and equity is not None and equity > 0:
        out["pb"] = div(price, equity / shares, 2)
    mcap = price * shares if shares else None
    out["ps"] = div(mcap, _v(base, "revenue"), 2)
    eb = ebitda(base)
    if eb is None:
        out["notes"].append("EV/EBITDA unavailable: no depreciation & amortisation line.")
    elif mcap is not None and _v(base, "total_debt") is not None and _v(base, "cash") is not None:
        ev = mcap + _v(base, "total_debt") - _v(base, "cash")  # type: ignore[operator]
        out["ev_ebitda"] = div(ev, eb, 2) if eb > 0 else None
    out["dividend_yield"] = div(_v(base, "dividends_per_share"), price)
    if estimates:
        est = estimates[0]
        e_eps = _v(est, "eps")
        if e_eps is not None and e_eps > 0:
            out["forward_pe"] = div(price, e_eps, 2)
            out["forward_pe_basis"] = (f"ESTIMATE: {est['period_type']} ending "
                                       f"{est['period_end'].isoformat()} from {est['source']}")
    return out


def percentile_rank(values: list[float], x: float) -> float | None:
    """Share of `values` strictly below x plus half the ties (0..1)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    below = sum(1 for v in vals if v < x)
    ties = sum(1 for v in vals if v == x)
    return round((below + 0.5 * ties) / len(vals), 4)


def quality_flags(p: dict[str, Any]) -> list[str]:
    """Internal consistency checks of one statement."""
    flags = []
    rev, gp, oi = _v(p, "revenue"), _v(p, "gross_profit"), _v(p, "operating_income")
    ni, eps = _v(p, "net_income"), _v(p, "eps")
    if rev is not None and rev < 0:
        flags.append("NEGATIVE_REVENUE")
    if rev is not None and gp is not None and gp > rev:
        flags.append("GROSS_PROFIT_EXCEEDS_REVENUE")
    if gp is not None and oi is not None and oi > gp:
        flags.append("OPERATING_INCOME_EXCEEDS_GROSS_PROFIT")
    if rev is not None and rev > 0 and ni is not None and ni > rev:
        flags.append("NET_INCOME_EXCEEDS_REVENUE")
    if ni is not None and eps is not None and ni != 0 and eps != 0 and (ni > 0) != (eps > 0):
        flags.append("EPS_SIGN_MISMATCH")
    sh = _v(p, "shares_outstanding")
    if ni is not None and eps is not None and sh and eps != 0:
        implied = ni / sh
        if abs(implied - eps) / abs(eps) > D("0.25"):
            flags.append("EPS_INCONSISTENT_WITH_NET_INCOME")
    ca, cl = _v(p, "current_assets"), _v(p, "current_liabilities")
    ta = _v(p, "total_assets")
    if ca is not None and ta is not None and ca > ta:
        flags.append("CURRENT_ASSETS_EXCEED_TOTAL_ASSETS")
    if cl is not None and cl < 0:
        flags.append("NEGATIVE_CURRENT_LIABILITIES")
    return flags


def coverage_flags(periods: list[dict[str, Any]], ts_date: date, stale_days: int) -> list[str]:
    """Missing quarters and stale data, relative to the decision date."""
    flags = []
    actual = [p for p in periods if p["value_basis"] != "ESTIMATED"]
    if not actual:
        return ["NO_ACTUAL_STATEMENTS"]
    latest = max(p["period_end"] for p in actual)
    if (ts_date - latest).days > stale_days:
        flags.append(f"STALE: latest period {latest.isoformat()} is "
                     f"{(ts_date - latest).days} days old (limit {stale_days})")
    qs = _quarters(actual)
    for a, b in zip(qs[:-1], qs[1:], strict=True):
        if (b["period_end"] - a["period_end"]).days > 110:
            flags.append(f"MISSING_QUARTER between {a['period_end'].isoformat()} and "
                         f"{b['period_end'].isoformat()}")
    if len(qs) < 4:
        flags.append(f"ONLY_{len(qs)}_QUARTERS: trailing-twelve-month ratios need 4")
    return flags


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def explain(r: dict[str, Any], ret: dict[str, Any], val: dict[str, Any],
            peers: dict[str, Any]) -> dict[str, list[str]]:
    """Supporting and contradicting factors, each quoting its number."""
    sup, con = [], []
    g = r.get("revenue_growth_yoy")
    if g is not None:
        (sup if g > 0 else con).append(f"Revenue growth year on year {_pct(g)}.")
    m = r.get("operating_margin")
    if m is not None:
        (sup if m > 0 else con).append(f"Operating margin {_pct(m)}.")
    e = r.get("eps_growth_yoy")
    if e is not None:
        (sup if e > 0 else con).append(f"EPS growth year on year {_pct(e)}.")
    de = r.get("debt_to_equity")
    if de is not None:
        (con if de > 1.5 else sup).append(f"Debt/equity {de:.2f}.")
    ic = r.get("interest_coverage")
    if ic is not None:
        (con if ic < 3 else sup).append(f"Interest coverage {ic:.1f}×.")
    cc = r.get("cash_conversion")
    if cc is not None:
        (sup if cc >= 0.8 else con).append(f"Cash conversion (OCF/net income) {cc:.2f}.")
    if r.get("earnings_quality") == "LOW":
        con.append(f"Accruals ratio {r['accruals_ratio']:.2f} is above "
                   f"{ACCRUALS_LOW_QUALITY}: earnings rely on accruals more than cash.")
    roe = ret.get("roe")
    if roe is not None:
        (sup if roe >= 0.12 else con).append(f"Return on equity {_pct(roe)}.")
    pe_rank = peers.get("percentiles", {}).get("pe")
    if pe_rank is not None and val.get("pe") is not None:
        line = (f"P/E {val['pe']:.1f} is at the {pe_rank * 100:.0f}th percentile of "
                f"{peers['count']} sector peers.")
        if pe_rank > 0.75:
            con.append(line + " Expensive relative to peers.")
        elif pe_rank < 0.25:
            sup.append(line + " Cheap relative to peers.")
    return {"supporting": sup, "contradicting": con}
