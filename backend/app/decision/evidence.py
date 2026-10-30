"""Evidence collection and the transparent, unweighted family-vote rule.

Evidence families (each casts at most ONE vote, +1 bullish / -1 bearish / 0 neutral,
None = unavailable):

- trend, momentum, volume: the technical families of app.indicators.evidence (the
  indicators inside a family are already collapsed to one vote there);
- candles: CONFIRMED candlestick / chart patterns of the last RECENT_PATTERN_BARS bars;
- structure: the latest CONFIRMED SMC event (BOS / CHOCH / liquidity sweep / failed
  breakout) of the last STRUCTURE_BARS bars;
- model: a PROMOTED model's P(UP) - P(DOWN) beyond MODEL_EDGE. Absent without a model.

Families flagged as correlated (a cross-family indicator pair with |rho| above the
evidence threshold) are merged into one group, and each group casts one vote (the sign
of its members' sum). The rule then needs a strict majority of the AVAILABLE groups and
at least MIN_GROUPS available groups. Weights are not used: the label is
UNWEIGHTED_UNVALIDATED (see app.decision.weights for the only way weights can be used).

Volatility, regime, fundamentals, event risk, liquidity and costs never vote: they are
gates or context.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

TECH_FAMILIES = ("trend", "momentum", "volume")
EVENT_FAMILIES = ("candles", "structure")
FAMILIES = (*TECH_FAMILIES, *EVENT_FAMILIES, "model")
RECENT_PATTERN_BARS = 5
STRUCTURE_BARS = 10
STRUCTURE_NAMES = ("BOS", "CHOCH")
MODEL_EDGE = 0.10
MIN_GROUPS = 3
WEIGHTS_LABEL = "UNWEIGHTED_UNVALIDATED"
RULE = (f"One vote per independent evidence family; families with a cross-family correlated "
        f"indicator pair are merged into one group. A direction passes when it holds a strict "
        f"majority of the available groups (neutral groups count against it) and at least "
        f"{MIN_GROUPS} groups are available. No weights are applied.")

_DIR = {"BULLISH": 1, "BEARISH": -1}


def _item(source: str, family: str, statement: str, value: Any, ts: datetime | None,
          vote: int | None = None) -> dict[str, Any]:
    return {"source": source, "family": family, "statement": statement, "value": value,
            "timestamp": ts, "vote": vote}


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


# ------------------------------------------------------------------ per family


def technical_items(technical: dict[str, Any], ts: datetime | None
                    ) -> tuple[dict[str, int | None], list[dict[str, Any]]]:
    votes: dict[str, int | None] = {}
    items = []
    for f in technical.get("families", []):
        fam = f["family"]
        if fam not in TECH_FAMILIES:
            continue
        avail = f.get("available", 0)
        votes[fam] = f["vote"] if avail else None
        if not avail:
            continue
        members = ", ".join(f"{m['indicator']} {m.get('reading', 'n/a')}" for m in f["members"]
                            if m.get("vote") is not None)
        items.append(_item("technical", fam, f"{fam.title()} family {f['bias'].lower()} "
                           f"({avail} indicator(s), agreement {f.get('agreement')}).",
                           members, ts, f["vote"]))
    return votes, items


def pattern_items(patterns: list[dict[str, Any]], n: int, ts_of: Any
                  ) -> tuple[int | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Vote from CONFIRMED patterns whose confirmation bar is within the recent window.
    Unconfirmed (PENDING / INVALIDATED / EXPIRED) patterns are returned as ignored."""
    used, ignored = [], []
    for p in patterns:
        conf = p.get("confirmation") or {}
        status = conf.get("status") or ("CONFIRMED" if p.get("kind") == "chart" else "PENDING")
        idx = conf.get("index") if conf.get("index") is not None else p.get("confirmed_index")
        if idx is None or idx < n - RECENT_PATTERN_BARS:
            continue
        direction = conf.get("direction") or p.get("direction")
        if status != "CONFIRMED":
            ignored.append(_item("candlestick", "candles", f"{p['name']} ignored: "
                                 f"{status.lower()}, not confirmed.", status, p.get("ts")))
            continue
        if direction not in _DIR:
            continue
        used.append(_item("candlestick" if p.get("kind") != "chart" else "chart_pattern",
                          "candles", f"Confirmed {direction.lower()} {p['name']} "
                          f"{n - 1 - idx} bar(s) ago.", conf.get("rule"),
                          conf.get("ts") or p.get("confirmed_ts"), _DIR[direction]))
    if not used:
        return None, [], ignored
    return _sign(sum(i["vote"] for i in used)), used, ignored


def structure_items(smc: dict[str, Any], n: int
                    ) -> tuple[int | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Vote from the most recent confirmed structure event (later structure supersedes)."""
    events = [e for e in smc.get("structure_events", []) if e["name"] in STRUCTURE_NAMES]
    events += smc.get("liquidity_sweeps", []) + smc.get("failed_breakouts", [])
    recent = [e for e in events if e.get("confirmed_index") is not None
              and n - 1 - STRUCTURE_BARS <= e["confirmed_index"] <= n - 1
              and e.get("direction") in _DIR]
    if not recent:
        return None, [], None
    recent.sort(key=lambda e: e["confirmed_index"])
    last = recent[-1]
    items = [_item("price_action", "structure",
                   f"{e['direction'].title()} {e['name']} confirmed "
                   f"{n - 1 - e['confirmed_index']} bar(s) ago.", e.get("level"),
                   e.get("confirmed_ts"), _DIR[e["direction"]]) for e in recent[-3:]]
    return _DIR[last["direction"]], items, last


def model_items(prediction: dict[str, Any] | None, ts: datetime | None
                ) -> tuple[int | None, list[dict[str, Any]], dict[str, Any]]:
    """A promoted model's view. Never invented: null probabilities stay null."""
    probs = (prediction or {}).get("probabilities")
    if not probs:
        reason = ((prediction or {}).get("reason")
                  or "No promoted, calibrated model for this instrument and timeframe.")
        return None, [], {"probabilities": None, "reason": reason, "source": None,
                          "model_version": (prediction or {}).get("model_version")}
    edge = probs.get("UP", 0.0) - probs.get("DOWN", 0.0)
    vote = 1 if edge >= MODEL_EDGE else -1 if edge <= -MODEL_EDGE else 0
    view = {"probabilities": probs, "reason": None, "source": "PROMOTED_MODEL",
            "model_version": prediction.get("model_version"),  # type: ignore[union-attr]
            "model_name": prediction.get("model_name"),  # type: ignore[union-attr]
            "uncertainty": prediction.get("uncertainty"),  # type: ignore[union-attr]
            "calibration_status": prediction.get("calibration_status")}  # type: ignore[union-attr]
    return vote, [_item("ai_model", "model", f"Promoted model P(UP) − P(DOWN) = {edge:+.2f} "
                        f"(vote only beyond ±{MODEL_EDGE}).", probs, ts, vote)], view


# ------------------------------------------------------------------ combination


def correlated_groups(pairs: list[dict[str, Any]]) -> list[set[str]]:
    """Union of families linked by any cross-family correlated indicator pair."""
    groups: list[set[str]] = [{f} for f in FAMILIES]
    for p in pairs:
        fa, fb = (p.get("families") or [None, None])[:2]
        if p.get("same_family") or fa not in FAMILIES or fb not in FAMILIES:
            continue
        ga = next(g for g in groups if fa in g)
        gb = next(g for g in groups if fb in g)
        if ga is not gb:
            ga |= gb
            groups.remove(gb)
    return groups


def combine(votes: dict[str, int | None], groups: list[set[str]] | None = None,
            side: str | None = None) -> dict[str, Any]:
    """The unweighted majority-of-available rule. `side` (CE/PE) forces the direction
    being tested; otherwise the side with more group votes is tested."""
    groups = groups or [{f} for f in FAMILIES]
    out_groups = []
    for g in groups:
        avail = {f: votes[f] for f in sorted(g) if votes.get(f) is not None}
        if not avail:
            continue
        out_groups.append({"families": sorted(g), "vote": _sign(sum(avail.values())),
                           "merged": len(g) > 1 and len(avail) > 1})
    n = len(out_groups)
    bull = sum(1 for g in out_groups if g["vote"] > 0)
    bear = sum(1 for g in out_groups if g["vote"] < 0)
    if side in ("CE", "PE"):
        direction = 1 if side == "CE" else -1
    else:
        direction = 1 if bull > bear else -1 if bear > bull else 0
    support = bull if direction > 0 else bear if direction < 0 else 0
    against = bear if direction > 0 else bull if direction < 0 else 0
    reasons = []
    passed = True
    if n < MIN_GROUPS:
        passed = False
        reasons.append(f"Only {n} independent evidence group(s) available; at least "
                       f"{MIN_GROUPS} are needed.")
    if direction == 0:
        passed = False
        reasons.append(f"Evidence is split or neutral ({bull} bullish, {bear} bearish of {n}).")
    elif support * 2 <= n:
        passed = False
        reasons.append(f"{support} of {n} evidence groups support "
                       f"{'bullish' if direction > 0 else 'bearish'}; a strict majority is "
                       "needed.")
    return {
        "weights": WEIGHTS_LABEL, "rule": RULE, "groups": out_groups,
        "available_groups": n, "bullish": bull, "bearish": bear,
        "direction": {1: "BULLISH", -1: "BEARISH", 0: "NONE"}[direction],
        "side": {1: "CE", -1: "PE", 0: None}[direction],
        "support": support, "against": against, "passed": passed,
        "conflicting": bull > 0 and bear > 0, "reasons": reasons,
    }


def collect(*, technical: dict[str, Any], patterns: list[dict[str, Any]], smc: dict[str, Any],
            prediction: dict[str, Any] | None, n: int, ts: datetime | None,
            ts_of: Any = None) -> dict[str, Any]:
    """Every family vote with its evidence items (pure)."""
    votes, items = technical_items(technical, ts)
    pv, pitems, ignored = pattern_items(patterns, n, ts_of)
    sv, sitems, last_structure = structure_items(smc, n)
    mv, mitems, model_view = model_items(prediction, ts)
    votes |= {"candles": pv, "structure": sv, "model": mv}
    return {"votes": votes, "items": items + pitems + sitems + mitems, "ignored": ignored,
            "last_structure": last_structure, "model": model_view,
            "groups": correlated_groups(technical.get("correlated_pairs", []))}


def split(items: list[dict[str, Any]], direction: str) -> tuple[list[dict], list[dict]]:
    """Supporting vs conflicting items for the tested direction (neutral = neither)."""
    d = _DIR.get(direction, 0)
    sup = [i for i in items if d and i.get("vote") == d]
    con = [i for i in items if d and i.get("vote") == -d]
    return sup, con
