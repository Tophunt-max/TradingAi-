"""Performance metrics. Formulas are documented in docs/backtesting.md."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

TRADING_DAYS = 252


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), nd)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: Sequence[float]) -> float:
    """Sample standard deviation (ddof = 1)."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def max_consecutive_losses(nets: Sequence[float]) -> int:
    best = cur = 0
    for x in nets:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def drawdown(equity: Sequence[float]) -> tuple[float, float]:
    """(max drawdown in rupees, max drawdown as % of the running peak)."""
    peak = -math.inf
    dd_abs = dd_pct = 0.0
    for e in equity:
        peak = max(peak, e)
        dd = peak - e
        dd_abs = max(dd_abs, dd)
        if peak > 0:
            dd_pct = max(dd_pct, dd / peak * 100)
    return dd_abs, dd_pct


def daily_ratios(daily_pnl: Sequence[float], capital: float) -> dict[str, float | None]:
    """Annualised Sharpe and Sortino on daily net P&L / capital (rf = 0)."""
    if len(daily_pnl) < 2 or capital <= 0:
        return {"sharpe": None, "sortino": None, "days": len(daily_pnl)}
    r = [p / capital for p in daily_pnl]
    m, sd = _mean(r), _std(r)
    downside = math.sqrt(_mean([min(x, 0.0) ** 2 for x in r]))
    ann = math.sqrt(TRADING_DAYS)
    return {"sharpe": _r(m / sd * ann, 3) if sd > 0 else None,
            "sortino": _r(m / downside * ann, 3) if downside > 0 else None,
            "days": len(daily_pnl)}


def trade_metrics(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    nets = [float(t["net"]) for t in trades]
    gross = sum(float(t["gross"]) for t in trades)
    charges = sum(float(t["charges"]) for t in trades)
    net = sum(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    sd = _std(nets)
    return {
        "trades": n,
        "gross_pnl": _r(gross),
        "charges_total": _r(charges),
        "net_pnl": _r(net),
        "cost_contribution_pct": _r(charges / abs(gross) * 100) if gross else None,
        "slippage_total": _r(sum(float(t.get("slippage", 0)) for t in trades)),
        "gap_slippage_total": _r(sum(float(t.get("gap_slippage", 0)) for t in trades)),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": _r(len(wins) / n * 100) if n else None,
        "profit_factor": _r(sum(wins) / abs(sum(losses)), 3) if losses else None,
        "expectancy": _r(net / n) if n else None,
        "avg_winner": _r(_mean(wins)) if wins else None,
        "avg_loser": _r(_mean(losses)) if losses else None,
        "max_consecutive_losses": max_consecutive_losses(nets),
        "per_trade_sharpe": _r(_mean(nets) / sd, 3) if n >= 2 and sd > 0 else None,
        "turnover": _r(sum((float(t["entry_price"]) + float(t["exit_price"])) * int(t["qty"])
                           for t in trades)),
    }


def group_summary(trades: Sequence[dict[str, Any]],
                  key: Callable[[dict[str, Any]], str]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        groups.setdefault(key(t), []).append(t)
    keep = ("trades", "net_pnl", "win_rate_pct", "expectancy", "profit_factor")
    out = {}
    for k in sorted(groups):
        m = trade_metrics(groups[k])
        out[k] = {f: m[f] for f in keep}
    return out


def stability(window_metrics: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Spread of per-window metrics (sample std); high values mean unstable results."""
    out: dict[str, Any] = {"windows": len(window_metrics)}
    for f in ("net_pnl", "win_rate_pct", "expectancy", "profit_factor"):
        xs = [float(m[f]) for m in window_metrics if m.get(f) is not None]
        out[f"{f}_mean"] = _r(_mean(xs), 3) if xs else None
        out[f"{f}_std"] = _r(_std(xs), 3) if len(xs) >= 2 else None
    nets = [float(m["net_pnl"] or 0) for m in window_metrics]
    out["profitable_windows_pct"] = _r(sum(x > 0 for x in nets) / len(nets) * 100) if nets \
        else None
    return out


def downsample(points: Sequence[Any], max_points: int = 200) -> list[Any]:
    if len(points) <= max_points:
        return list(points)
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(k * step)] for k in range(max_points)]
