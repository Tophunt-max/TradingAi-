"""Validation tooling: chronological splits, walk-forward, sensitivity, Monte Carlo."""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from app.backtesting import engine as E
from app.backtesting import metrics as M

OBJECTIVES = ("net_pnl", "profit_factor", "expectancy", "sharpe")
PERCENTILES = (5, 25, 50, 75, 95)


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Wall-clock guard so a request cannot run unbounded."""

    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds
        self.seconds = seconds

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise BudgetExceeded(f"Computation exceeded {self.seconds:.0f} s; reduce bars or "
                                 "grid size.")


def chronological_split(n: int, train_pct: float = 60, validation_pct: float = 20,
                        ) -> dict[str, tuple[int, int]]:
    """[start, end) index ranges in time order: train, then validation, then test."""
    if not (train_pct > 0 and validation_pct >= 0 and train_pct + validation_pct < 100):
        raise ValueError("train_pct + validation_pct must be below 100.")
    a = int(n * train_pct / 100)
    b = int(n * (train_pct + validation_pct) / 100)
    return {"train": (0, a), "validation": (a, b), "test": (b, n)}


@dataclass(frozen=True)
class Grid:
    stops_pct: tuple[float, ...]
    targets_pct: tuple[float, ...]
    thresholds: tuple[float, ...] | None = None

    @property
    def size(self) -> int:
        return len(self.stops_pct) * len(self.targets_pct) * len(self.thresholds or (None,))


def threshold_index(strat: E.Strategy) -> int | None:
    """The first entry condition with a numeric value (the tunable threshold)."""
    return next((k for k, c in enumerate(strat.conditions) if c.get("value") is not None), None)


def with_params(base: E.Strategy, stop_pct: float, target_pct: float,
                threshold: float | None) -> E.Strategy:
    conds = copy.deepcopy(base.conditions)
    if threshold is not None:
        k = threshold_index(base)
        if k is None:
            raise ValueError("This strategy has no numeric threshold to vary.")
        conds[k]["value"] = threshold
    return replace(base, conditions=conds, stop=E.Level("PERCENT", stop_pct),
                   target=E.Level("PERCENT", target_pct))


def variants(base: E.Strategy, grid: Grid) -> list[tuple[dict[str, Any], E.Strategy]]:
    out = []
    for sp in grid.stops_pct:
        for tp in grid.targets_pct:
            for th in grid.thresholds or (None,):
                params = {"stop_pct": sp, "target_pct": tp}
                if th is not None:
                    params["threshold"] = th
                out.append((params, with_params(base, sp, tp, th)))
    return out


def objective_value(s: dict[str, Any], objective: str, min_trades: int) -> float:
    if s["trades"] < min_trades:
        return -math.inf
    if objective == "profit_factor" and s["profit_factor"] is None:
        return 1e6 if s["trades"] and s["losses"] == 0 else -math.inf
    v = s.get(objective)
    return -math.inf if v is None else float(v)


def optimise(prep: E.Prepared, cands: list[tuple[dict[str, Any], E.Strategy]],
             exe: E.Execution, risk: E.Risk, *, seed: int, start: int, end: int,
             objective: str, min_trades: int, budget: Budget | None = None,
             ) -> tuple[int, dict[str, Any]]:
    """Best candidate on [start, end) by objective; ties keep the first in grid order."""
    best_k, best_v, best_s = 0, -math.inf, None
    for k, (_, strat) in enumerate(cands):
        if budget:
            budget.check()
        s = E.summary(E.run(prep, strat, exe, risk, seed=seed, start=start, end=end))
        v = objective_value(s, objective, min_trades)
        if best_s is None or v > best_v:
            best_k, best_v, best_s = k, v, s
    assert best_s is not None
    return best_k, best_s


def merge(raws: list[dict[str, Any]]) -> dict[str, Any]:
    """Concatenate window results into one out-of-sample record."""
    capital = raws[0]["capital"]
    out: dict[str, Any] = {"trades": [], "equity": [], "daily": {}, "bars": 0,
                           "bars_in_market": 0, "signals": 0, "suppressed": {}, "rejected": {},
                           "capital": capital}
    offset = 0.0
    for r in raws:
        out["trades"] += r["trades"]
        out["equity"] += [(t, e + offset) for t, e in r["equity"]]
        if r["equity"]:
            offset += r["equity"][-1][1] - capital
        for d, v in r["daily"].items():
            out["daily"][d] = out["daily"].get(d, 0.0) + v
        for f in ("bars", "bars_in_market", "signals"):
            out[f] += r[f]
        for f in ("suppressed", "rejected"):
            for k, v in r[f].items():
                out[f][k] = out[f].get(k, 0) + v
    return out


def _span(prep: E.Prepared, a: int, b: int) -> dict[str, Any]:
    ts = prep.ctx.ts
    return {"start_index": a, "end_index": b, "from": E._iso(ts[a]) if a < len(ts) else None,
            "to": E._iso(ts[b - 1]) if 0 < b <= len(ts) else None, "bars": b - a}


def walk_forward(prep: E.Prepared, base: E.Strategy, grid: Grid, exe: E.Execution,
                 risk: E.Risk, *, seed: int, train_bars: int, test_bars: int,
                 holdout_pct: float, objective: str, min_trades: int, max_windows: int,
                 budget: Budget | None = None) -> dict[str, Any]:
    """Rolling walk-forward. Parameters are chosen on each train window only and
    applied to the following test window. The aggregate uses test windows only.
    The final holdout is excluded from every window and evaluated once at the end."""
    n = len(prep)
    hold_start = n - int(n * holdout_pct / 100)
    cands = variants(base, grid)
    windows, raws = [], []
    s = 0
    while s + train_bars + test_bars <= hold_start and len(windows) < max_windows:
        tr, te = (s, s + train_bars), (s + train_bars, s + train_bars + test_bars)
        k, train_sum = optimise(prep, cands, exe, risk, seed=seed, start=tr[0], end=tr[1],
                                objective=objective, min_trades=min_trades, budget=budget)
        raw = E.run(prep, cands[k][1], exe, risk, seed=seed + len(windows) + 1,
                    start=te[0], end=te[1])
        raws.append(raw)
        windows.append({"window": len(windows) + 1, "train": _span(prep, *tr),
                        "test": _span(prep, *te), "chosen_params": cands[k][0],
                        "train_metrics": _brief(train_sum), "test_metrics": _brief(E.summary(raw)),
                        "test_trades": len(raw["trades"])})
        s += test_bars
    if not windows:
        raise ValueError(f"Not enough bars for one walk-forward window: need train_bars + "
                         f"test_bars = {train_bars + test_bars} before the holdout, have "
                         f"{hold_start}.")
    oos = merge(raws)
    holdout = None
    if hold_start < n:
        h0 = max(0, hold_start - train_bars)
        k, _ = optimise(prep, cands, exe, risk, seed=seed, start=h0, end=hold_start,
                        objective=objective, min_trades=min_trades, budget=budget)
        hraw = E.run(prep, cands[k][1], exe, risk, seed=seed + 10_000, start=hold_start, end=n)
        holdout = {"span": _span(prep, hold_start, n), "chosen_params": cands[k][0],
                   "params_chosen_on": _span(prep, h0, hold_start),
                   "metrics": _brief(E.summary(hraw)), "evaluated_once": True,
                   "note": "Final holdout: parameters were chosen before it was evaluated. "
                           "Re-running with different settings after looking at this result "
                           "turns it into in-sample data."}
    return {"windows": windows, "oos_raw": oos, "oos_metrics": E.summary(oos),
            "stability": M.stability([w["test_metrics"] for w in windows]),
            "holdout": holdout, "grid_size": grid.size, "objective": objective,
            "min_trades": min_trades,
            "splits": {"walk_forward": _span(prep, 0, hold_start),
                       "holdout": _span(prep, hold_start, n)}}


_BRIEF = ("trades", "net_pnl", "gross_pnl", "charges_total", "win_rate_pct", "profit_factor",
          "expectancy", "max_drawdown", "sharpe")


def _brief(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s[k] for k in _BRIEF}


def sensitivity(prep: E.Prepared, base: E.Strategy, grid: Grid, exe: E.Execution,
                risk: E.Risk, *, seed: int, budget: Budget | None = None) -> dict[str, Any]:
    cells = []
    for params, strat in variants(base, grid):
        if budget:
            budget.check()
        s = E.summary(E.run(prep, strat, exe, risk, seed=seed))
        cells.append({"params": params, **{k: s[k] for k in (*_BRIEF, "max_drawdown_pct")}})
    nets = [float(c["net_pnl"] or 0) for c in cells]
    return {"cells": cells,
            "axes": {"stop_pct": list(grid.stops_pct), "target_pct": list(grid.targets_pct),
                     "threshold": list(grid.thresholds) if grid.thresholds else None},
            "stability": {"profitable_cells_pct": round(sum(x > 0 for x in nets) / len(nets)
                                                        * 100, 2),
                          "net_pnl_mean": round(float(np.mean(nets)), 2),
                          "net_pnl_std": round(float(np.std(nets, ddof=1)), 2)
                          if len(nets) > 1 else None},
            "in_sample": True,
            "note": "Every cell is evaluated on the same bars (in-sample). Use the surface to "
                    "judge whether results depend on a narrow parameter choice, not to pick "
                    "the best cell."}


def monte_carlo(nets: list[float], *, simulations: int, seed: int) -> dict[str, Any]:
    """Bootstrap the trade sequence: resample trades with replacement."""
    if not nets:
        raise ValueError("There are no trades to resample.")
    arr = np.asarray(nets, dtype=float)
    rng = np.random.default_rng(seed)
    samples = arr[rng.integers(0, len(arr), size=(simulations, len(arr)))]
    cum = np.concatenate([np.zeros((simulations, 1)), samples.cumsum(axis=1)], axis=1)
    totals = cum[:, -1]
    dd = (np.maximum.accumulate(cum, axis=1) - cum).max(axis=1)

    def pct(x: np.ndarray) -> dict[str, float]:
        return {f"p{p}": round(float(np.percentile(x, p)), 2) for p in PERCENTILES}

    return {"simulations": simulations, "trades_per_simulation": len(arr), "seed": seed,
            "net_pnl": pct(totals), "max_drawdown": pct(dd),
            "mean_net_pnl": round(float(totals.mean()), 2),
            "probability_of_loss": round(float((totals < 0).mean()), 4),
            "method": "Bootstrap: each simulation draws the same number of trades with "
                      "replacement from the backtest's net trade P&L (numpy PCG64, seeded). "
                      "Assumes trades are independent; ignores regime clustering."}
