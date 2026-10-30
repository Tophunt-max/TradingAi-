"""Decision engine: combines every analysis layer into one DecisionReport.

`market_view` runs the causal analysis on closed candles; `decide` is pure: it takes the
evidence, gates, the options candidate, the sizing proposal and the read-only risk
dry-run, and returns the report. The engine never places, reserves or approves an order.
A TRADE_PROPOSAL carries a `paper_order_request` that the user may submit to
POST /api/v1/orders/paper after confirming; the risk manager checks it again then.

Decision states:
- NO_TRADE: any blocking item (data FAIL, no-trade regime, HIGH event, conflicting or
  opposing evidence, expected net <= 0, ineligible contract, risk dry-run fails,
  account cannot trade).
- WATCH: nothing blocks, but a required item is missing (evidence majority not reached
  without conflict, no option chain / no modelled expected net, sizing 0 lots is a
  blocker, not a watch).
- TRADE_PROPOSAL: every requirement holds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.analysis import context as C
from app.analysis import patterns as PAT
from app.analysis import regime as R
from app.analysis import smc as SMC
from app.analysis.pipeline import technical_view
from app.decision import evidence as EV

VERSION = "decision-v1.0"
SETUPS = ("TREND_CONTINUATION", "BREAKOUT", "REVERSAL", "RANGE_FADE", "NONE")
DISCLAIMER = ("Research proposal only. The engine never places an order. A paper order is "
              "sent only after you confirm it, and the independent risk manager checks it "
              "again at that moment. No outcome is guaranteed.")


def market_view(candles: list[dict[str, Any]], timeframe: str, quality_status: str,
                event_risk_level: str | None) -> dict[str, Any]:
    ctx = C.build(candles, intraday=timeframe != "1d", timeframe=timeframe)
    n = len(ctx)
    pats = PAT.all_patterns(ctx)
    smc = SMC.analyse(ctx, quality=quality_status)
    regime_v2 = R.detect_v2(ctx, smc=smc, event_risk_level=event_risk_level,
                            quality_status=quality_status)
    technical = technical_view(ctx, n)
    return {"ctx": ctx, "n": n, "patterns": pats, "smc": smc, "regime_v2": regime_v2,
            "technical": technical, "last_ts": ctx.ts[-1], "close": ctx.c[-1],
            "atr": ctx.s["atr14"][-1]}


def classify_setup(direction: str, regime_v2: dict[str, Any] | None,
                   last_structure: dict[str, Any] | None,
                   premium_discount: dict[str, Any] | None) -> tuple[str, str]:
    """First matching rule wins (documented order)."""
    if direction not in ("BULLISH", "BEARISH"):
        return "NONE", "No directional evidence."
    state = (regime_v2 or {}).get("state")
    st_dir = (last_structure or {}).get("direction")
    st_name = (last_structure or {}).get("name")
    up = direction == "BULLISH"
    if state == "BREAKOUT" or (st_name == "BOS" and st_dir == direction
                               and state not in ("STRONG_UPTREND", "STRONG_DOWNTREND")):
        return "BREAKOUT", "Breakout regime or a fresh break of structure in the direction."
    if state == ("STRONG_UPTREND" if up else "STRONG_DOWNTREND"):
        return "TREND_CONTINUATION", "Strong trend regime in the same direction."
    if state == "REVERSAL" or (st_name == "CHOCH" and st_dir == direction):
        return "REVERSAL", "Reversal regime or a change of character in the direction."
    zone = (premium_discount or {}).get("zone")
    if state in ("RANGE", "LOW_VOLATILITY") and zone in ("PREMIUM", "DISCOUNT") \
            and (zone == "DISCOUNT") == up:
        return "RANGE_FADE", f"Range regime with price in the {zone.lower()} half."
    return "NONE", "Evidence has a direction but no setup rule matched the regime."


def _invalidation(last_structure: dict[str, Any] | None, patterns_used: list[dict[str, Any]],
                  setup_levels: dict[str, Any] | None, side: str | None) -> list[dict[str, Any]]:
    out = []
    if last_structure and (last_structure.get("invalidation") or {}).get("level") is not None:
        inv = last_structure["invalidation"]
        out.append({"source": "price_action", "level": inv["level"], "rule": inv["rule"]})
    if setup_levels and setup_levels.get("stop_loss") is not None:
        out.append({"source": "swing", "level": setup_levels["stop_loss"],
                    "rule": f"A close {'below' if side == 'CE' else 'above'} "
                            f"{setup_levels['stop_loss']} ({setup_levels['stop_basis']})."})
    for p in patterns_used[:2]:
        out.append({"source": p["source"], "level": None, "rule": f"{p['statement']} "
                    "Invalidated by a close beyond the pattern's opposite extreme."})
    out.append({"source": "regime", "level": None,
                "rule": "A regime change to UNKNOWN, ABNORMAL or EVENT_UNCERTAINTY."})
    out.append({"source": "data", "level": None, "rule": "A data-quality failure or stale "
                                                         "quotes."})
    return out


def decide(*, symbol: str, timeframe: str, account_mode: str, is_demo: bool,
           data_source: str, quality: dict[str, Any], view: dict[str, Any],
           collected: dict[str, Any], combined: dict[str, Any], event_risk: dict[str, Any],
           fundamentals: dict[str, Any], options: dict[str, Any] | None,
           candidate: dict[str, Any] | None, sizing: dict[str, Any] | None,
           risk: dict[str, Any] | None, setup_levels: dict[str, Any] | None,
           execution: dict[str, Any], weights_info: dict[str, Any],
           requested_side: str, now: datetime) -> dict[str, Any]:
    blockers: list[str] = []
    watch: list[str] = []
    warnings: list[str] = []
    q_status = quality.get("status", "OK")
    regime_v2 = view.get("regime_v2") or {}

    # Data quality
    if q_status == "FAIL":
        blockers.append("Market-data quality check failed.")
    elif q_status == "WARN":
        warnings.append("Market-data quality has warnings: "
                        + "; ".join(i.get("message", i.get("code", "")) for i in
                                    quality.get("issues", [])[:3]))
    if is_demo:
        warnings.append("Input data is synthetic DEMO data (SIMULATED); nothing here is "
                        "evidence about the real market.")
    # Regime
    if regime_v2.get("no_trade", True):
        blockers.append(f"Regime {regime_v2.get('state', 'UNKNOWN')} is a no-trade state.")
    # Event gate
    action = (event_risk or {}).get("action", "NONE")
    if action == "NO_TRADE":
        blockers.append("Event-risk gate: HIGH-importance event in the window.")
    elif action == "REDUCE_EXPOSURE":
        warnings.append("Event-risk gate: MEDIUM-importance event; exposure capped at 1 lot.")
    # Evidence
    side = combined.get("side")
    if not combined["passed"]:
        # Bullish and bearish groups both present, or the tested side is outvoted: the
        # evidence disagrees, which blocks. Mostly-neutral / too little evidence: WATCH.
        if combined["conflicting"] or combined.get("against", 0) > combined.get("support", 0):
            blockers += ["Evidence conflicts: " + r for r in combined["reasons"]]
        else:
            watch += ["Evidence rule not met: " + r for r in combined["reasons"]]
    # Fundamentals: context only
    f_status = fundamentals.get("status", "NO_DATA")
    if f_status not in ("OK", "INDEX", "NO_DATA"):
        warnings.append(f"Fundamentals status {f_status}.")
    # Options
    if side is None:
        watch.append("No directional side to evaluate an option contract for.")
    elif options is None:
        watch.append("Option chain unavailable for the proposed side.")
    elif candidate is None:
        blockers.append("No eligible option contract for the proposed side.")
    else:
        en = candidate.get("expected_net") or {}
        if not candidate.get("eligible"):
            blockers += [f"Contract not eligible: {b}" for b in candidate["blocked_reasons"]]
        if not en.get("available"):
            watch.append("Expected net after costs is not modelled: "
                         + str(en.get("reason") or "no IV or mid price."))
        elif en["expected_net_pnl"] <= 0:
            blockers.append(f"Expected net after costs is ₹{en['expected_net_pnl']:.2f} per lot "
                            "(≤ 0): costs and decay outweigh the modelled edge.")
        for fl in (candidate.get("risks") or {}).get("flags", []):
            warnings.append(f"Liquidity/risk flag {fl['code']}: {fl['message']}")
    # Sizing
    if candidate is not None and sizing is not None and sizing["lots"] < 1:
        blockers.append("Position sizing gives 0 lots: " + " ".join(sizing["notes"]))
    # Risk dry-run
    if risk is not None and not risk["allowed"]:
        blockers += [f"Risk manager (dry run): {r}" for r in risk["reasons"]]
    # Account / execution
    if not execution["available"]:
        blockers.append(execution["reason"])

    decision = "NO_TRADE" if blockers else "WATCH" if watch else "TRADE_PROPOSAL"
    direction = combined["direction"]
    setup, setup_basis = classify_setup(direction, regime_v2, collected.get("last_structure"),
                                        (view.get("smc") or {}).get("premium_discount"))
    sup, con = EV.split(collected["items"], direction)
    pats_used = [i for i in sup if i["family"] == "candles"]
    en = (candidate or {}).get("expected_net") or {}
    proposal = None
    if decision == "TRADE_PROPOSAL" and candidate is not None and sizing is not None:
        proposal = {
            "paper_order_request": {
                "underlying": symbol, "option_type": side,
                "expiry": options["expiry"].isoformat() if hasattr(options["expiry"],
                                                                   "isoformat")
                else str(options["expiry"]),
                "strike": str(candidate["strike"]), "lots": sizing["lots"],
                "stop_loss_pct": sizing["stop_pct"], "target_pct": sizing["target_pct"]},
            "endpoint": "POST /api/v1/orders/paper",
            "requires_user_confirmation": True,
            "note": "Add a fresh idempotency_key. The risk manager re-checks every limit at "
                    "order time; this proposal reserves nothing.",
        }
    model = collected["model"]
    return {
        "version": VERSION, "symbol": symbol, "timeframe": timeframe, "as_of": now,
        "bar_ts": view.get("last_ts"), "account_mode": account_mode,
        "data_source": data_source, "label": "SIMULATED" if is_demo else data_source,
        "decision": decision, "requested_side": requested_side, "side": side,
        "directional_view": {
            "direction": direction,
            "probabilities": model["probabilities"],
            "probability_source": model["source"],
            "probability_reason": model["reason"],
            "model_version": model.get("model_version"),
            "evidence_direction_is_not_a_probability": True,
        },
        "setup": {"classification": setup, "basis": setup_basis},
        "evidence": {
            "combination": combined, "weights": weights_info,
            "votes": collected["votes"],
            "supporting": sup, "conflicting": con,
            "ignored": collected["ignored"],
            "context": [
                {"source": "regime", "family": "regime",
                 "statement": f"Regime v2 {regime_v2.get('state')} "
                              f"({regime_v2.get('calibration_status', 'UNCALIBRATED')}).",
                 "value": regime_v2.get("confidence"), "timestamp": view.get("last_ts")},
                {"source": "fundamentals", "family": "fundamentals",
                 "statement": f"Fundamentals {f_status}: context only for intraday options.",
                 "value": fundamentals.get("message"), "timestamp": fundamentals.get("as_of")},
                {"source": "events", "family": "event_risk",
                 "statement": f"Event-risk level {(event_risk or {}).get('level', 'NONE')}.",
                 "value": (event_risk or {}).get("reasons", [])[:3],
                 "timestamp": (event_risk or {}).get("as_of")},
                {"source": "technical", "family": "volatility",
                 "statement": "Volatility is context, not a vote.",
                 "value": (view.get("technical") or {}).get("volatility_state"),
                 "timestamp": view.get("last_ts")},
            ],
        },
        "data_quality": {"status": q_status, "warnings": warnings},
        "regime": {k: regime_v2.get(k) for k in ("state", "confidence", "calibration_status",
                                                 "no_trade", "reasons")},
        "event_risk": event_risk,
        "fundamentals": {"status": f_status, "message": fundamentals.get("message"),
                         "role": "CONTEXT_ONLY"},
        "options": None if candidate is None else {
            "contract": {k: candidate.get(k) for k in ("trading_symbol", "strike", "side",
                                                       "moneyness", "bid", "ask", "mid", "iv",
                                                       "oi", "volume")},
            "expiry": options["expiry"] if options else None,
            "lot_size": options.get("lot_size") if options else None,
            "lot_size_source": options.get("lot_size_source") if options else None,
            "eligible": candidate.get("eligible"),
            "blocked_reasons": candidate.get("blocked_reasons"),
            "underlying_direction": candidate.get("underlying_direction"),
            "premium_path": candidate.get("premium_path"),
            "expected_net": en, "risks": candidate.get("risks"),
            "assumptions": options.get("assumptions") if options else None,
        },
        "entry_conditions": _entry_conditions(side, candidate, sizing, setup_levels),
        "invalidation": _invalidation(collected.get("last_structure"), pats_used,
                                      setup_levels, side),
        "stop_loss": None if sizing is None else {
            "premium_pct": sizing["stop_pct"], "premium": sizing["stop_premium"],
            "underlying_level": (setup_levels or {}).get("stop_loss"),
            "underlying_basis": (setup_levels or {}).get("stop_basis")},
        "target": None if sizing is None else {
            "premium_pct": sizing["target_pct"], "premium": sizing["target_premium"],
            "underlying_levels": (setup_levels or {}).get("targets")},
        "sizing": sizing,
        "expected_outcome": None if not en else {
            "expected_net_pnl_per_lot": en.get("expected_net_pnl"),
            "costs_per_lot": en.get("costs"), "net_pnl_quantiles": en.get("net_pnl_quantiles"),
            "p_target_first": ((candidate or {}).get("premium_path") or {}).get(
                "p_target_first"),
            "p_stop_first": ((candidate or {}).get("premium_path") or {}).get("p_stop_first"),
            "label": en.get("label", "MODEL_ESTIMATE"),
            "assumptions": (options or {}).get("assumptions")},
        "risk_reward": None if sizing is None else {
            "premium": sizing["premium_risk_reward"],
            "underlying": (setup_levels or {}).get("risk_reward")},
        "risk_check": risk,
        "execution": execution,
        "proposal": proposal,
        "no_trade_reasons": list(dict.fromkeys(blockers)),
        "watch_reasons": list(dict.fromkeys(watch)),
        "decision_disclaimer": DISCLAIMER,
    }


def _entry_conditions(side: str | None, candidate: dict[str, Any] | None,
                      sizing: dict[str, Any] | None,
                      setup_levels: dict[str, Any] | None) -> list[str]:
    if side is None:
        return []
    out = []
    if setup_levels and setup_levels.get("entry_zone"):
        lo, hi = setup_levels["entry_zone"]
        out.append(f"Underlying between {lo} and {hi} (current closed bar ± 0.1 ATR).")
    if candidate is not None:
        out.append(f"Buy {candidate.get('trading_symbol') or side} at or below ask "
                   f"{candidate.get('ask')} with a fresh two-sided quote.")
    if sizing is not None:
        out.append(f"{sizing['lots']} lot(s); planned risk ₹{sizing['planned_risk']} within "
                   f"the ₹{sizing['risk_budget']} budget.")
    out.append("Every global risk check passes at order time (re-checked by the risk "
               "manager).")
    return out
