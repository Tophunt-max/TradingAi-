"""Scoring rules, calibration and bootstrap confidence intervals (pure numpy)."""

from __future__ import annotations

from typing import Any

import numpy as np

EPS = 1e-12
CLASSES = ["DOWN", "SIDEWAYS", "UP"]
BINARY_CLASSES = ["DOWN", "UP"]


def target_of(mv: Any) -> str:
    """'three_class' (default) or 'binary', as recorded by the training recipe."""
    return (mv.metrics or {}).get("target") or (mv.params or {}).get("target") or "three_class"


def classes_of(mv: Any) -> list[str]:
    return BINARY_CLASSES if target_of(mv) == "binary" else CLASSES


def prediction_matrix(preds: list[Any], classes: list[str]) -> np.ndarray:
    cols = {"DOWN": "p_down", "SIDEWAYS": "p_sideways", "UP": "p_up"}
    return np.array([[getattr(p, cols[c]) for c in classes] for p in preds])


def per_sample_log_loss(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.clip(P[np.arange(len(y)), y], EPS, 1.0))


def per_sample_brier(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.sum((P - np.eye(P.shape[1])[y]) ** 2, axis=1)


def block_bootstrap_mean_ci(x: np.ndarray, block: int, resamples: int = 1000,
                            alpha: float = 0.05, seed: int = 0) -> dict[str, float]:
    """Moving-block bootstrap CI of the mean.

    Overlapping forecast horizons make consecutive outcomes dependent, so blocks of
    length `block` (the horizon) are resampled instead of single points.
    """
    n = len(x)
    if n == 0:
        return {"mean": float("nan"), "lo": float("nan"), "hi": float("nan")}
    block = max(1, min(block, n))
    rng = np.random.default_rng(seed)
    starts_max = n - block + 1
    k = int(np.ceil(n / block))
    means = np.empty(resamples)
    for i in range(resamples):
        starts = rng.integers(0, starts_max, size=k)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        means[i] = x[idx].mean()
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return {"mean": round(float(x.mean()), 6), "lo": round(float(lo), 6),
            "hi": round(float(hi), 6)}


def improvement_vs_baseline(P: np.ndarray, B: np.ndarray, y: np.ndarray, block: int,
                            resamples: int = 1000) -> dict[str, Any]:
    """Per-sample (baseline − model) loss differences; positive means the model is better."""
    ll = per_sample_log_loss(B, y) - per_sample_log_loss(P, y)
    br = per_sample_brier(B, y) - per_sample_brier(P, y)
    return {"n": int(len(y)), "block": int(block), "resamples": resamples,
            "log_loss_improvement": block_bootstrap_mean_ci(ll, block, resamples),
            "brier_improvement": block_bootstrap_mean_ci(br, block, resamples, seed=1)}


def calibration_table(P: np.ndarray, y: np.ndarray, bins: int = 10) -> tuple[float, list[dict]]:
    """Reliability table on top-class confidence and expected calibration error."""
    if len(y) == 0:
        return float("nan"), []
    conf, pred = P.max(axis=1), P.argmax(axis=1)
    hit = (pred == y).astype(float)
    edges = np.linspace(1 / P.shape[1], 1.0, bins + 1)
    ece, rows = 0.0, []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf >= lo) & ((conf < hi) | (hi == 1.0))
        if not m.any():
            continue
        acc, cf = float(hit[m].mean()), float(conf[m].mean())
        ece += float(m.mean()) * abs(acc - cf)
        rows.append({"bucket": f"{lo:.2f}-{hi:.2f}", "predicted": round(cf, 4),
                     "realized": round(acc, 4), "count": int(m.sum())})
    return round(ece, 5), rows


def class_reliability(P: np.ndarray, y: np.ndarray, bins: int = 5,
                      classes: list[str] | None = None) -> dict[str, list[dict]]:
    """Per-class predicted probability vs realized frequency."""
    out: dict[str, list[dict]] = {}
    edges = np.linspace(0, 1, bins + 1)
    for c, name in enumerate(classes or CLASSES):
        p, hit = P[:, c], (y == c).astype(float)
        rows = []
        for lo, hi in zip(edges[:-1], edges[1:], strict=True):
            m = (p >= lo) & ((p < hi) | (hi == 1.0))
            if m.any():
                rows.append({"bucket": f"{lo:.1f}-{hi:.1f}", "predicted": round(float(p[m].mean()),
                                                                                  4),
                             "realized": round(float(hit[m].mean()), 4), "count": int(m.sum())})
        out[name] = rows
    return out
