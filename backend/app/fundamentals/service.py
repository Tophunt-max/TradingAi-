"""Fundamentals payload for one symbol at one decision time.

Everything is point-in-time: the instrument's own reports, its peers' reports and
the price all use only information available at `ts`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utcnow
from app.fundamentals import ratios as RT
from app.fundamentals import store

logger = logging.getLogger("app.fundamentals")
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY", "BANKEX"}
INTRADAY_NOTE = ("For intraday options, fundamentals are context and event-risk inputs only; "
                 "they do not replace price, liquidity, volatility and execution analysis.")


def _jsonable(p: dict[str, Any]) -> dict[str, Any]:
    return {**{k: v for k, v in p.items() if k != "items"},
            "period_end": p["period_end"].isoformat(),
            "items": {k: None if v is None else str(v) for k, v in p["items"].items()}}


def split(periods: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    actual = [p for p in periods if p["value_basis"] in ("REPORTED", "RESTATED")]
    est = [p for p in periods if p["value_basis"] == "ESTIMATED"]
    return actual, est


def analyse_periods(periods: list[dict[str, Any]], price: Decimal | None, ts: datetime,
                    stale_days: int) -> dict[str, Any]:
    """Ratios, valuation and flags from visible periods (pure)."""
    actual, est = split(periods)
    flags = RT.coverage_flags(actual, ts.date(), stale_days)
    for p in actual[-8:]:
        flags += [f"{f} ({p['period_type']} {p['period_end'].isoformat()})"
                  for f in p["quality_flags"] if not f.startswith("REVISION_OF")]
    if any(p["revision"] > 0 for p in actual):
        flags.append("RESTATED: at least one period has a later revision; latest is used")
    latest = actual[-1] if actual else None
    ratios = RT.period_ratios(latest, actual) if latest else None
    base = RT.ttm(actual)
    if base is None:
        fys = [p for p in actual if p["period_type"] == "FY"]
        base = fys[-1] if fys else None
    ret = RT.return_ratios(base)
    future_est = sorted([e for e in est
                         if latest is None or e["period_end"] > latest["period_end"]],
                        key=lambda e: e["period_end"])
    val = RT.valuation(price, base, future_est)
    return {"latest": latest, "base": base, "ratios": ratios, "returns": ret,
            "valuation": val, "flags": flags, "estimates": future_est}


def _price_at(provider: Any, symbol: str, ts: datetime, now: datetime) -> tuple[Any, str, str]:
    """(price, basis, source). Live quote for 'now', else the last daily close <= ts."""
    try:
        if now - ts < timedelta(minutes=5):
            r = provider.quotes([symbol], "ALL")
            q = next((x for x in r.data if x.get("symbol") == symbol), None)
            if q and q.get("last") is not None:
                return Decimal(str(q["last"])), "Last traded price", r.source
        r = provider.candles(symbol, "1d", 400)
        rows = [c for c in r.data if ensure_utc(c["ts"]) <= ts]
        if rows:
            return Decimal(str(rows[-1]["close"])), \
                f"Daily close {rows[-1]['ts'].date().isoformat()}", r.source
    except Exception:  # noqa: BLE001 - a missing price only disables valuation
        logger.warning("fundamentals_price_unavailable", extra={"symbol": symbol})
    return None, "Price unavailable", "UNAVAILABLE"


def _peer_view(db: Session, sector: str | None, symbol: str, ts: datetime,
               own: dict[str, Any], stale_days: int) -> dict[str, Any]:
    if not sector:
        return {"sector": None, "count": 0, "percentiles": {},
                "note": "No sector classification imported."}
    names = store.peers(db, sector, symbol, ts)
    metrics = ("operating_margin", "net_margin", "revenue_growth_yoy", "debt_to_equity")
    vals: dict[str, list[float]] = {m: [] for m in metrics}
    used = 0
    for n in names:
        per = store.as_of(db, n, ts)
        if not per:
            continue
        a = analyse_periods(per, None, ts, stale_days)
        if a["ratios"] is None:
            continue
        used += 1
        for m in metrics:
            if a["ratios"].get(m) is not None:
                vals[m].append(a["ratios"][m])
    pct: dict[str, float | None] = {}
    if own.get("ratios"):
        for m in metrics:
            x = own["ratios"].get(m)
            pct[m] = RT.percentile_rank(vals[m], x) if x is not None and vals[m] else None
    return {"sector": sector, "count": used, "percentiles": pct,
            "note": "Peers' data restricted to what was available at the same time."}


def _peer_pe(db: Session, provider: Any, sector: str | None, symbol: str, ts: datetime,
             now: datetime, own_pe: float | None, stale_days: int) -> tuple[float | None, int]:
    if not sector or own_pe is None:
        return None, 0
    pes = []
    for n in store.peers(db, sector, symbol, ts):
        per = store.as_of(db, n, ts)
        if not per:
            continue
        price, _, _ = _price_at(provider, n, ts, now)
        pe = analyse_periods(per, price, ts, stale_days)["valuation"]["pe"]
        if pe is not None:
            pes.append(pe)
    return (RT.percentile_rank(pes, own_pe) if pes else None), len(pes)


def _own_history(db: Session, provider: Any, symbol: str, ts: datetime, now: datetime,
                 own_pe: float | None, stale_days: int) -> dict[str, Any]:
    """P/E at each earlier report-availability time, using only data known then."""
    if own_pe is None:
        return {"pe_percentile": None, "points": 0}
    actual = [p for p in store.as_of(db, symbol, ts) if p["value_basis"] != "ESTIMATED"]
    pes = []
    for p in actual[:-1]:
        t = ensure_utc(p["available_at"])
        if t >= ts:
            continue
        price, _, _ = _price_at(provider, symbol, t, now)
        pe = analyse_periods(store.as_of(db, symbol, t), price, t, stale_days)["valuation"]["pe"]
        if pe is not None:
            pes.append(pe)
    return {"pe_percentile": RT.percentile_rank(pes, own_pe) if pes else None,
            "points": len(pes),
            "note": "Own trailing P/E at each earlier report date, prices from daily candles."}


def build(db: Session, provider: Any, symbol: str, ts: datetime | None,
          stale_days: int) -> dict[str, Any]:
    now = utcnow()
    ts = ensure_utc(ts) if ts else now
    prof = store.profile(db, symbol, ts)
    prof_out = None if prof is None else {
        "name": prof.name, "sector": prof.sector, "industry": prof.industry,
        "peer_group": prof.peer_group, "is_index": prof.is_index, "source": prof.source}
    base = {"symbol": symbol, "as_of": ts, "profile": prof_out, "periods": [], "ratios": None,
            "returns": None, "valuation": None, "price": None, "peers": None, "history": None,
            "flags": [], "explanation": {"supporting": [], "contradicting": [],
                                         "context": [INTRADAY_NOTE]},
            "corporate_actions": [a_out(a) for a in store.actions(db, symbol, ts)]}
    if symbol in INDEX_SYMBOLS or (prof is not None and prof.is_index):
        return {**base, **index_view(db, symbol, ts, prof, stale_days)}
    periods = store.as_of(db, symbol, ts)
    if not periods:
        return {**base, "status": "NO_DATA",
                "message": ("No fundamentals have been imported for this instrument. No "
                            "licensed fundamentals source is configured, and the system never "
                            "fabricates statements."),
                "flags": ["NO_DATA"]}
    price, basis, src = _price_at(provider, symbol, ts, now)
    a = analyse_periods(periods, price, ts, stale_days)
    peers = _peer_view(db, prof.sector if prof else None, symbol, ts, a, stale_days)
    peer_pe, n_pe = _peer_pe(db, provider, prof.sector if prof else None, symbol, ts, now,
                             a["valuation"]["pe"], stale_days)
    peers["percentiles"]["pe"] = peer_pe
    peers["pe_peers"] = n_pe
    hist = _own_history(db, provider, symbol, ts, now, a["valuation"]["pe"], stale_days)
    exp = RT.explain(a["ratios"] or {}, a["returns"], a["valuation"], peers)
    label = "SIMULATED" if src == "DEMO" else src
    context = [INTRADAY_NOTE]
    if label == "SIMULATED" and price is not None:
        context.append("The price used for valuation is SIMULATED (DEMO market data), so "
                       "the multiples are illustrative only.")
    if not prof_out:
        context.append("No company profile (sector/peers) imported.")
    return {**base, "status": "OK", "message": None,
            "periods": [_jsonable(p) for p in periods],
            "ratios": a["ratios"], "returns": a["returns"],
            "ttm_basis": a["base"].get("basis") if a["base"] else None,
            "valuation": a["valuation"],
            "price": {"value": None if price is None else str(price), "basis": basis,
                      "label": label},
            "peers": peers, "history": hist, "flags": a["flags"],
            "explanation": {**exp, "context": context}}


def a_out(a: Any) -> dict[str, Any]:
    return {"action_type": a.action_type, "ex_date": a.ex_date.isoformat(),
            "record_date": a.record_date.isoformat() if a.record_date else None,
            "ratio": f"{a.ratio_from}:{a.ratio_to}" if a.ratio_from and a.ratio_to else None,
            "amount": None if a.amount is None else str(a.amount),
            "announced_at": a.announced_at, "source": a.source}


def index_view(db: Session, symbol: str, ts: datetime, prof: Any,
               stale_days: int) -> dict[str, Any]:
    msg = ("Index derivatives: context comes from index-level, constituent, breadth and macro "
           "data, not one company's statements.")
    cons = (prof.constituents if prof is not None else None) or []
    if not cons:
        return {"status": "INDEX", "message": msg + " No constituent list with weights has "
                "been imported, so a constituent-weighted view is unavailable.",
                "constituents": None}
    agg: dict[str, list[tuple[float, float]]] = {}
    covered = 0.0
    for c in cons:
        per = store.as_of(db, c["symbol"], ts)
        if not per:
            continue
        r = analyse_periods(per, None, ts, stale_days)["ratios"]
        if not r:
            continue
        covered += c["weight"]
        for m in ("revenue_growth_yoy", "operating_margin", "net_margin", "debt_to_equity"):
            if r.get(m) is not None:
                agg.setdefault(m, []).append((c["weight"], r[m]))
    weighted = {m: round(sum(w * v for w, v in xs) / sum(w for w, _ in xs), 4)
                for m, xs in agg.items()}
    return {"status": "INDEX", "message": msg,
            "constituents": {"listed": len(cons), "weight_with_data": round(covered, 4),
                             "weighted_ratios": weighted,
                             "note": "Weighted by the imported constituent weights, using only "
                                     "constituents with data at this time."}}
