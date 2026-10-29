"""Evidence families and indicator-correlation checks.

Most indicators are transforms of the same closes, so counting each as an
independent "confirmation" overstates the evidence. Indicators are grouped
into families and each family casts ONE vote. A correlation matrix over the
recent window flags pairs that move together even across families.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

FAMILIES: dict[str, tuple[str, ...]] = {
    "trend": ("ema_stack", "supertrend", "adx_di", "ichimoku"),
    "momentum": ("rsi", "macd", "stochastic", "cci", "roc"),
    "volume": ("obv", "rvol", "vwap"),
    "volatility": ("atr", "bollinger", "hv"),
}
# Volatility says how much price moves, not which way: it is context, not a vote.
DIRECTIONAL = ("trend", "momentum", "volume")
CORRELATION_THRESHOLD = 0.8
MIN_OVERLAP = 20


def family_of(indicator: str) -> str | None:
    return next((f for f, members in FAMILIES.items() if indicator in members), None)


def family_votes(votes: Mapping[str, int | None]) -> dict[str, Any]:
    """Collapse per-indicator votes (+1 / 0 / −1, None = unavailable) into one
    vote per family: the sign of the sum of its available member votes."""
    families = []
    net = 0
    for fam, members in FAMILIES.items():
        got = {m: votes[m] for m in members if m in votes and votes[m] is not None}
        total = sum(got.values())  # type: ignore[arg-type]
        vote = (1 if total > 0 else -1 if total < 0 else 0) if fam in DIRECTIONAL else 0
        agree = (sum(1 for v in got.values() if v == vote and v != 0) / len(got)
                 if got and fam in DIRECTIONAL else None)
        if fam in DIRECTIONAL:
            net += vote
        families.append({
            "family": fam, "directional": fam in DIRECTIONAL, "vote": vote,
            "bias": "BULLISH" if vote > 0 else "BEARISH" if vote < 0 else "NEUTRAL",
            "members": [{"indicator": m, "vote": got.get(m)} for m in members],
            "available": len(got), "agreement": None if agree is None else round(agree, 2),
        })
    return {"families": families, "net_vote": net, "max_votes": len(DIRECTIONAL),
            "method": "One vote per evidence family (sign of the sum of member votes); "
                      "volatility is context only."}


def correlation_matrix(series: Mapping[str, Sequence[float | None]], window: int = 100,
                       min_overlap: int = MIN_OVERLAP) -> dict[str, Any]:
    """Pearson correlation of the last `window` values of each series, computed
    on the bars where every series in the pair has a value."""
    names = list(series)
    tails = {k: np.array([np.nan if v is None else float(v) for v in series[k][-window:]])
             for k in names}
    matrix: dict[str, dict[str, float | None]] = {k: {} for k in names}
    for a_i, a in enumerate(names):
        for b in names[a_i:]:
            x, y = tails[a], tails[b]
            m = min(len(x), len(y))
            x, y = x[-m:], y[-m:]
            ok = ~(np.isnan(x) | np.isnan(y))
            rho: float | None = None
            if ok.sum() >= min_overlap and np.std(x[ok]) > 0 and np.std(y[ok]) > 0:
                rho = float(np.corrcoef(x[ok], y[ok])[0, 1])
            matrix[a][b] = matrix[b][a] = None if rho is None else round(rho, 3)
    return {"names": names, "matrix": matrix, "window": window}


def correlated_pairs(corr: dict[str, Any], threshold: float = CORRELATION_THRESHOLD
                     ) -> list[dict[str, Any]]:
    names, m = corr["names"], corr["matrix"]
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            rho = m[a][b]
            if rho is not None and abs(rho) > threshold:
                fa, fb = family_of(a), family_of(b)
                out.append({"a": a, "b": b, "rho": rho, "same_family": fa == fb,
                            "families": [fa, fb]})
    return sorted(out, key=lambda p: -abs(p["rho"]))
