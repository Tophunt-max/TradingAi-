"""Event-risk gate (pure). Used by the analysis pipeline and, later, the decision layer.

Rules (deterministic, documented in docs/fundamentals-events.md):
* Only events with `available_at <= ts` count (no lookahead).
* DUPLICATE events are ignored; an UPDATE replaces the event it supersedes once the
  update itself is available.
* An event is relevant if its scope is MARKET, or its symbols contain the
  instrument, its underlying index, or the instrument's sector.
* It is in the window if `ts - after <= event_time <= ts + before`.
* Any HIGH-importance event in the window → level HIGH, action NO_TRADE.
  Otherwise any MEDIUM → ELEVATED, REDUCE_EXPOSURE. LOW events are listed only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.time import ensure_utc

RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def visible(events: list[dict[str, Any]], ts: datetime) -> list[dict[str, Any]]:
    """Events knowable at ts, with duplicates dropped and superseded versions replaced."""
    ts = ensure_utc(ts)
    known = [e for e in events if ensure_utc(e["available_at"]) <= ts
             and e.get("novelty") != "DUPLICATE"]
    superseded = {e["supersedes_id"] for e in known if e.get("supersedes_id")}
    return [e for e in known if e["id"] not in superseded]


def relevant(e: dict[str, Any], symbol: str, underlying: str | None,
             sector: str | None) -> bool:
    if e["scope"] == "MARKET":
        return True
    keys = {s.upper() for s in (e.get("symbols") or [])}
    wanted = {symbol.upper()} | ({underlying.upper()} if underlying else set()) \
        | ({sector.upper()} if sector else set())
    return bool(keys & wanted)


def event_risk(events: list[dict[str, Any]], symbol: str, underlying: str | None,
               ts: datetime, window_before: timedelta, window_after: timedelta,
               sector: str | None = None) -> dict[str, Any]:
    ts = ensure_utc(ts)
    hits = []
    for e in visible(events, ts):
        et = ensure_utc(e["event_time"])
        if not (ts - window_after <= et <= ts + window_before):
            continue
        if not relevant(e, symbol, underlying, sector):
            continue
        hits.append(e)
    hits.sort(key=lambda e: (-RANK[e["importance"]], ensure_utc(e["event_time"])))
    top = max((RANK[e["importance"]] for e in hits), default=-1)
    level, action = ("HIGH", "NO_TRADE") if top == 2 else \
        ("ELEVATED", "REDUCE_EXPOSURE") if top == 1 else ("NONE", "NONE")
    reasons = []
    for e in hits:
        et = ensure_utc(e["event_time"])
        when = "in" if et >= ts else "ago"
        mins = int(abs((et - ts).total_seconds()) // 60)
        rel = f"{mins} min {when}" if when == "in" else f"{mins} min ago"
        reasons.append(f"{e['importance']} {e['category'].replace('_', ' ').lower()}: "
                       f"{e['title']} ({rel}, source {e['source']}).")
    if not hits:
        reasons.append("No imported event in the window. Only imported events are known; "
                       "absence of data is not absence of risk.")
    return {"level": level, "action": action, "reasons": reasons,
            "events": [{k: e.get(k) for k in ("id", "category", "scope", "title",
                                               "importance", "event_time", "source",
                                               "source_reliability", "novelty")}
                       for e in hits],
            "window": {"before_minutes": int(window_before.total_seconds() // 60),
                       "after_minutes": int(window_after.total_seconds() // 60)},
            "as_of": ts}
