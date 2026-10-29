"""Evaluates a bot's configured entry conditions on the last closed candle.

The bot configuration is the source of truth: exactly the conditions the user
sees in the builder are what the worker runs. Crossover-type conditions are
true only on the candle where the cross happens, so one move gives one signal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.analysis import patterns as PAT
from app.analysis import smc as SMC
from app.analysis.context import Ctx
from app.indicators import core as ind

# indicator -> operators the evaluator understands
SUPPORTED: dict[str, set[str]] = {
    "RSI": {"GT", "LT", "CROSS_ABOVE", "CROSS_BELOW"},
    "EMA_CROSS": {"CROSS_ABOVE", "CROSS_BELOW", "GT", "LT"},
    "VWAP": {"PRICE_ABOVE", "PRICE_BELOW", "CLOSE_ABOVE", "CLOSE_BELOW", "CROSS_ABOVE",
             "CROSS_BELOW"},
    "MACD": {"GT", "LT", "CROSS_ABOVE", "CROSS_BELOW"},
    "RELATIVE_VOLUME": {"GT", "LT"},
    "SUPERTREND": {"PRICE_ABOVE", "PRICE_BELOW", "CROSS_ABOVE", "CROSS_BELOW"},
    "RESISTANCE_BREAK": {"CLOSE_ABOVE", "CLOSE_BELOW"},
    "PATTERN": {"PRESENT"},
    "SMC_CHOCH": {"PRESENT"},
    "SMC_ORDER_BLOCK_RETEST": {"PRESENT"},
    "MODEL_PROBABILITY": {"GT"},
}
NEEDS_VALUE = {"GT", "LT"}

_MIRROR_OP = {"GT": "LT", "LT": "GT", "CROSS_ABOVE": "CROSS_BELOW", "CROSS_BELOW": "CROSS_ABOVE",
              "PRICE_ABOVE": "PRICE_BELOW", "PRICE_BELOW": "PRICE_ABOVE",
              "CLOSE_ABOVE": "CLOSE_BELOW", "CLOSE_BELOW": "CLOSE_ABOVE"}
_MIRROR_PATTERN = {"BULLISH_ENGULFING": "BEARISH_ENGULFING", "HAMMER": "SHOOTING_STAR",
                   "MORNING_STAR": "EVENING_STAR", "DOUBLE_BOTTOM": "DOUBLE_TOP"}
_MIRROR_PATTERN |= {v: k for k, v in _MIRROR_PATTERN.items()}


def supported(cond: dict[str, Any]) -> str | None:
    """None if the evaluator can run this condition, else the reason it cannot."""
    ops = SUPPORTED.get(cond["indicator"])
    if ops is None:
        return f"Indicator {cond['indicator']} is not supported."
    if cond["operator"] not in ops:
        return f"{cond['indicator']} does not support {cond['operator']}."
    if cond["operator"] in NEEDS_VALUE and cond.get("value") is None:
        return f"{cond['indicator']} {cond['operator']} needs a value."
    if cond["indicator"] == "PATTERN" and not (cond.get("params") or {}).get("name"):
        return "PATTERN needs params.name."
    return None


def mirror(cond: dict[str, Any]) -> dict[str, Any]:
    """Bearish (PE) counterpart of a bullish (CE) condition."""
    out = {**cond, "params": dict(cond.get("params") or {})}
    if cond["indicator"] in ("SMC_CHOCH", "SMC_ORDER_BLOCK_RETEST", "MODEL_PROBABILITY",
                             "RELATIVE_VOLUME"):
        return out  # direction comes from the bot's CE/PE side, or is symmetric
    out["operator"] = _MIRROR_OP.get(cond["operator"], cond["operator"])
    if cond["indicator"] == "RSI" and cond.get("value") is not None:
        out["value"] = 100 - float(cond["value"])
    if cond["indicator"] == "MACD" and cond.get("value") is not None:
        out["value"] = -float(cond["value"])
    if cond["indicator"] == "PATTERN":
        name = str(out["params"].get("name", "")).upper()
        out["params"]["name"] = _MIRROR_PATTERN.get(name, name)
    return out


def _cross(a: list[Any], b: list[Any] | float, up: bool) -> bool | None:
    b0 = b if isinstance(b, int | float) else b[-2]
    b1 = b if isinstance(b, int | float) else b[-1]
    if len(a) < 2 or None in (a[-2], a[-1], b0, b1):
        return None
    return (a[-2] <= b0 and a[-1] > b1) if up else (a[-2] >= b0 and a[-1] < b1)


def _cmp(v: Any, op: str, value: float | None) -> bool | None:
    if v is None or value is None:
        return None
    return v > value if op == "GT" else v < value if op == "LT" else None


class Evaluator:
    """Lazily computes what conditions need (patterns, SMC, custom periods)."""

    def __init__(self, ctx: Ctx, side: str,
                 probability: Callable[[], dict[str, float] | None] | None = None) -> None:
        self.ctx, self.side, self._prob_fn = ctx, side, probability
        self._patterns: list[dict[str, Any]] | None = None
        self._smc: dict[str, Any] | None = None

    @property
    def patterns(self) -> list[dict[str, Any]]:
        if self._patterns is None:
            self._patterns = PAT.all_patterns(self.ctx)
        return self._patterns

    @property
    def smc(self) -> dict[str, Any]:
        if self._smc is None:
            self._smc = SMC.analyse(self.ctx)
        return self._smc

    def _ema(self, p: int) -> list[Any]:
        key = f"ema{p}"
        if key not in self.ctx.s:
            self.ctx.s[key] = ind.ema(self.ctx.c, p)
        return self.ctx.s[key]

    def _rsi(self, p: int) -> list[Any]:
        key = f"rsi{p}"
        if key not in self.ctx.s:
            self.ctx.s[key] = ind.rsi(self.ctx.c, p)
        return self.ctx.s[key]

    def evaluate(self, cond: dict[str, Any]) -> bool | None:
        if supported(cond):
            return None
        ctx, s, c = self.ctx, self.ctx.s, self.ctx.c
        name, op, val = cond["indicator"], cond["operator"], cond.get("value")
        params = cond.get("params") or {}
        last = len(ctx) - 1
        if name == "RSI":
            r = self._rsi(int(params.get("period", 14)))
            if op in NEEDS_VALUE:
                return _cmp(r[-1], op, val)
            return _cross(r, float(val if val is not None else 50), op == "CROSS_ABOVE")
        if name == "EMA_CROSS":
            fast = self._ema(int(params.get("fast", 9)))
            slow = self._ema(int(params.get("slow", 21)))
            if op in ("CROSS_ABOVE", "CROSS_BELOW"):
                return _cross(fast, slow, op == "CROSS_ABOVE")
            if fast[-1] is None or slow[-1] is None:
                return None
            return fast[-1] > slow[-1] if op == "GT" else fast[-1] < slow[-1]
        if name == "VWAP":
            v = s["vwap"]
            if op in ("CROSS_ABOVE", "CROSS_BELOW"):
                return _cross(c, v, op == "CROSS_ABOVE")
            if v[-1] is None:
                return None
            return c[-1] > v[-1] if op.endswith("ABOVE") else c[-1] < v[-1]
        if name == "MACD":
            if op in ("CROSS_ABOVE", "CROSS_BELOW"):
                return _cross(s["macd"], s["macd_signal"], op == "CROSS_ABOVE")
            return _cmp(s["macd_hist"][-1], op, val)
        if name == "RELATIVE_VOLUME":
            return _cmp(s["rvol20"][-1], op, val)
        if name == "SUPERTREND":
            d = s["supertrend_dir"]
            if d[-1] is None:
                return None
            if op == "PRICE_ABOVE":
                return d[-1] == 1
            if op == "PRICE_BELOW":
                return d[-1] == -1
            if d[-2] is None:
                return None
            return (d[-2] == -1 and d[-1] == 1) if op == "CROSS_ABOVE" else (
                d[-2] == 1 and d[-1] == -1)
        if name == "RESISTANCE_BREAK":
            ph, pl = PAT.pivots(ctx)
            # Only swings already confirmed before the last candle count.
            if op == "CLOSE_ABOVE":
                lvl = next((p for i, p in reversed(ph) if i + PAT.PIVOT_R < last), None)
                return None if lvl is None else c[-2] <= lvl < c[-1]
            lvl = next((p for i, p in reversed(pl) if i + PAT.PIVOT_R < last), None)
            return None if lvl is None else c[-2] >= lvl > c[-1]
        if name == "PATTERN":
            want = str(params["name"]).replace("_", " ").lower()
            return any(e["name"].lower() == want and e["confirmed_index"] == last
                       for e in self.patterns)
        bull = self.side == "CE"
        if name == "SMC_CHOCH":
            return any(e["name"] == "CHOCH" and e["confirmed_index"] == last
                       and e["direction"] == ("BULLISH" if bull else "BEARISH")
                       for e in self.smc["structure_events"])
        if name == "SMC_ORDER_BLOCK_RETEST":
            # Price trades into an order block that was untouched until this candle.
            return any(z["direction"] == ("BULLISH" if bull else "BEARISH")
                       and z["mitigated_index"] == last for z in self.smc["order_blocks"])
        if name == "MODEL_PROBABILITY":
            probs = self._prob_fn() if self._prob_fn else None
            if not probs:
                return None
            return probs["UP" if bull else "DOWN"] > float(val)  # type: ignore[arg-type]
        return None  # pragma: no cover


def describe(cond: dict[str, Any]) -> str:
    params = cond.get("params") or {}
    p = f" ({', '.join(str(v) for v in params.values())})" if params else ""
    v = f" {cond['value']}" if cond.get("value") is not None else ""
    op = cond["operator"].replace("_", " ").lower()
    return f"{cond['indicator'].replace('_', ' ')}{p} {op}{v}"
