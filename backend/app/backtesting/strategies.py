"""Which catalogue strategies can be backtested, and with which conditions."""

from __future__ import annotations

from typing import Any

from app.strategies.catalog import STRATEGIES, TEMPLATE_ORDER
from app.strategies.conditions import describe, mirror, supported

DEFAULT_STOP_PCT = 0.3
DEFAULT_TARGET_PCT = 0.6

NOT_BACKTESTABLE = {
    "ai_adaptive": "Needs model predictions made only with data before each bar. Backtest the "
                   "model through the model registry's walk-forward evaluation instead.",
    "dca_grid": "Research only: bounded-loss behaviour is not demonstrated.",
}
UNSUPPORTED_IN_BACKTEST = {"MODEL_PROBABILITY": "MODEL_PROBABILITY conditions cannot be "
                                                "backtested without point-in-time predictions."}


def check_conditions(conds: list[dict[str, Any]]) -> list[str]:
    problems = []
    for c in conds:
        why = UNSUPPORTED_IN_BACKTEST.get(c["indicator"]) or supported(c)
        if why:
            problems.append(why)
    return problems


def threshold_param(conds: list[dict[str, Any]]) -> dict[str, Any] | None:
    c = next((c for c in conds if c.get("value") is not None), None)
    return None if c is None else {"condition": describe(c), "default": c["value"]}


def catalog() -> list[dict[str, Any]]:
    out = []
    for sid in TEMPLATE_ORDER:
        meta = STRATEGIES[sid]
        conds = meta["default_entry"]
        reason = NOT_BACKTESTABLE.get(sid)
        if sid == "custom":
            reason = None
        out.append({
            "id": sid, "name": meta["name"], "version": meta["version"],
            "summary": meta["summary"], "timeframes": meta["timeframes"],
            "regimes": meta["regimes"], "backtestable": reason is None,
            "reason": reason, "requires_entry_conditions": sid == "custom",
            "default_entry": conds, "default_entry_pe": [mirror(c) for c in conds],
            "conditions": [describe(c) for c in conds],
            "params": {
                "stop_pct": {"default": DEFAULT_STOP_PCT, "min": 0.05, "max": 10,
                             "applies_to": "underlying price"},
                "target_pct": {"default": DEFAULT_TARGET_PCT, "min": 0.05, "max": 20,
                               "applies_to": "underlying price"},
                "threshold": threshold_param(conds),
            },
        })
    return out


def resolve(strategy_id: str, side: str,
            entry: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Entry conditions for a catalogue strategy (PE side uses the mirrored set)."""
    if strategy_id not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy_id}'.")
    if strategy_id in NOT_BACKTESTABLE:
        raise ValueError(NOT_BACKTESTABLE[strategy_id])
    if entry:
        return entry  # user-supplied conditions are used as given, like a bot config
    if strategy_id == "custom":
        raise ValueError("The custom strategy needs entry conditions.")
    conds = [dict(c, params=dict(c.get("params") or {}))
             for c in STRATEGIES[strategy_id]["default_entry"]]
    return conds if side == "CE" else [mirror(c) for c in conds]
