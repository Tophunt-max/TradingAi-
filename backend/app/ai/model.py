"""Baseline probabilistic model: standardised multinomial logistic regression.

Evaluated with purged, expanding-window walk-forward validation against a
class-frequency (climatology) baseline. Parameters are stored as plain JSON
(never pickle) so a model version is reproducible and safe to load.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression

from app.ai.features import CLASSES

ALGORITHM = "logreg-l2-v1"
HYPERPARAMS = {"C": 0.5, "max_iter": 2000}
EPS = 1e-12

# Promotion gate. All must pass on out-of-sample predictions.
GATE = {
    "min_oos_samples": 300,
    "max_logloss_ratio": 0.98,  # model log loss must be ≥ 2% better than the baseline
    "max_ece": 0.05,
    "min_class_share": 0.05,
}


@dataclass
class Params:
    mean: list[float]
    scale: list[float]
    coef: list[list[float]]
    intercept: list[float]

    def as_dict(self) -> dict[str, Any]:
        return {"mean": self.mean, "scale": self.scale, "coef": self.coef,
                "intercept": self.intercept}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Params:
        return cls(d["mean"], d["scale"], d["coef"], d["intercept"])


def fit(X: np.ndarray, y: np.ndarray, seed: int = 0, n_classes: int = 3) -> Params:
    if set(np.unique(y)) != set(range(n_classes)):
        raise ValueError(f"Training data must contain all {n_classes} classes.")
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale == 0] = 1.0
    clf = LogisticRegression(C=HYPERPARAMS["C"], max_iter=HYPERPARAMS["max_iter"],
                             random_state=seed)
    clf.fit((X - mean) / scale, y)
    assert list(clf.classes_) == list(range(n_classes))
    coef, intercept = clf.coef_, clf.intercept_
    if n_classes == 2:
        # sklearn stores one row (logit of class 1). softmax([-z/2, z/2]) == sigmoid(z),
        # so two symmetric rows reproduce it exactly with the shared softmax predictor.
        coef = np.vstack([-coef[0] / 2, coef[0] / 2])
        intercept = np.array([-intercept[0] / 2, intercept[0] / 2])
    return Params(mean.tolist(), scale.tolist(), coef.tolist(), intercept.tolist())


def predict_proba(p: Params, X: np.ndarray) -> np.ndarray:
    z = ((X - np.array(p.mean)) / np.array(p.scale)) @ np.array(p.coef).T + np.array(p.intercept)
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def log_loss(P: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], EPS, 1))))


def brier(P: np.ndarray, y: np.ndarray) -> float:
    onehot = np.eye(P.shape[1])[y]
    return float(np.mean(np.sum((P - onehot) ** 2, axis=1)))


def calibration(P: np.ndarray, y: np.ndarray, bins: int = 10) -> tuple[float, list[dict]]:
    """Expected calibration error on the top-class confidence, plus the curve."""
    conf = P.max(axis=1)
    pred = P.argmax(axis=1)
    correct = (pred == y).astype(float)
    edges = np.linspace(1 / P.shape[1], 1.0, bins + 1)
    ece, curve = 0.0, []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf >= lo) & ((conf < hi) | (hi == 1.0))
        if not m.any():
            continue
        acc, cf = float(correct[m].mean()), float(conf[m].mean())
        ece += m.mean() * abs(acc - cf)
        curve.append({"bin": f"{lo:.2f}-{hi:.2f}", "confidence": round(cf, 4),
                      "accuracy": round(acc, 4), "count": int(m.sum())})
    return float(ece), curve


FitPredict = Callable[[np.ndarray, np.ndarray, np.ndarray, int], np.ndarray]


def _logistic_fit_predict(n_classes: int) -> FitPredict:
    def fp(Xtr: np.ndarray, ytr: np.ndarray, Xte: np.ndarray, seed: int) -> np.ndarray:
        return predict_proba(fit(Xtr, ytr, seed=seed, n_classes=n_classes), Xte)
    return fp


def walk_forward(X: np.ndarray, y: np.ndarray, horizon: int, folds: int = 5,
                 initial_frac: float = 0.5, fit_predict: FitPredict | None = None,
                 n_classes: int = 3) -> dict[str, Any]:
    """Expanding-window walk-forward with a purge gap of `horizon` rows.

    Training rows for a fold end `horizon` rows before the fold's first test
    row, so no training label window overlaps a test row. `fit_predict`
    (default: this module's logistic regression) receives only the fold's
    training rows; any calibration split must be taken inside them.
    """
    fit_predict = fit_predict or _logistic_fit_predict(n_classes)
    n = len(y)
    start = int(n * initial_frac)
    size = max((n - start) // folds, 1)
    P_all, y_all, B_all, fold_info, test_idx = [], [], [], [], []
    for k in range(folds):
        t0 = start + k * size
        t1 = n if k == folds - 1 else min(t0 + size, n)
        train_end = t0 - horizon
        if t1 <= t0 or train_end < 50:
            continue
        Xtr, ytr = X[:train_end], y[:train_end]
        if set(np.unique(ytr)) != set(range(n_classes)):
            continue
        P = fit_predict(Xtr, ytr, X[t0:t1], k)
        prior = np.bincount(ytr, minlength=n_classes) / len(ytr)
        B = np.tile(prior, (t1 - t0, 1))
        P_all.append(P)
        B_all.append(B)
        y_all.append(y[t0:t1])
        test_idx.extend(range(t0, t1))
        fold_info.append({"fold": k + 1, "train_rows": int(train_end), "test_rows": int(t1 - t0),
                          "purge_rows": horizon, "test_start": int(t0), "test_end": int(t1)})
    if not P_all:
        raise ValueError("Not enough data for walk-forward validation.")
    P, B, yy = np.vstack(P_all), np.vstack(B_all), np.concatenate(y_all)
    return {"P": P, "B": B, "y": yy, "test_idx": test_idx, "folds": fold_info}


def evaluate(wf: dict[str, Any], regimes: list[str | None] | None = None,
             classes: list[str] | None = None) -> dict[str, Any]:
    P, B, y = wf["P"], wf["B"], wf["y"]
    classes = classes or CLASSES
    ece, curve = calibration(P, y)
    metrics = {
        "oos_samples": int(len(y)),
        "log_loss": round(log_loss(P, y), 5),
        "baseline_log_loss": round(log_loss(B, y), 5),
        "brier": round(brier(P, y), 5),
        "baseline_brier": round(brier(B, y), 5),
        "ece": round(ece, 5),
        "accuracy": round(float((P.argmax(axis=1) == y).mean()), 4),
        "class_share": {classes[i]: round(float((y == i).mean()), 4)
                        for i in range(len(classes))},
        "calibration_curve": curve,
        "folds": wf["folds"],
    }
    if regimes is not None:
        by: dict[str, dict[str, float]] = {}
        reg = np.array([r or "UNKNOWN" for r in regimes])
        for r in sorted(set(reg)):
            m = reg == r
            if m.sum() >= 20:
                by[r] = {"samples": int(m.sum()), "log_loss": round(log_loss(P[m], y[m]), 5),
                         "baseline_log_loss": round(log_loss(B[m], y[m]), 5)}
        metrics["by_regime"] = by
    return metrics


def gate(metrics: dict[str, Any], data_source: str) -> dict[str, Any]:
    checks = []

    def add(key: str, label: str, ok: bool, detail: str) -> None:
        checks.append({"key": key, "label": label, "passed": bool(ok), "detail": detail})

    add("samples", "Enough out-of-sample predictions",
        metrics["oos_samples"] >= GATE["min_oos_samples"],
        f"{metrics['oos_samples']} (need ≥ {GATE['min_oos_samples']}).")
    ratio = metrics["log_loss"] / metrics["baseline_log_loss"] if metrics["baseline_log_loss"] \
        else math.inf
    add("log_loss", "Beats the base-rate baseline (log loss)",
        ratio <= GATE["max_logloss_ratio"],
        f"{metrics['log_loss']:.4f} vs baseline {metrics['baseline_log_loss']:.4f} "
        f"(ratio {ratio:.3f}, need ≤ {GATE['max_logloss_ratio']}).")
    add("brier", "Beats the baseline (Brier score)", metrics["brier"] < metrics["baseline_brier"],
        f"{metrics['brier']:.4f} vs baseline {metrics['baseline_brier']:.4f}.")
    add("calibration", "Calibrated", metrics["ece"] <= GATE["max_ece"],
        f"Expected calibration error {metrics['ece']:.4f} (need ≤ {GATE['max_ece']}).")
    min_share = min(metrics["class_share"].values())
    add("classes", "All outcomes represented", min_share >= GATE["min_class_share"],
        f"Smallest class share {min_share:.1%} (need ≥ {GATE['min_class_share']:.0%}).")
    add("data", "Trained on real market data", data_source != "DEMO",
        "Synthetic DEMO data can never promote a model." if data_source == "DEMO"
        else f"Source: {data_source}.")
    vs = metrics.get("vs_logistic")
    if vs is not None:  # challengers must beat the logistic baseline, not just climatology
        add("beats_logistic", "Beats the logistic baseline (bootstrap CI)", vs["better"],
            f"Mean log-loss difference {vs['mean_diff']:+.4f}, 95% CI "
            f"[{vs['ci_low']:+.4f}, {vs['ci_high']:+.4f}] (need the upper bound < 0).")
    return {"passed": all(c["passed"] for c in checks), "checks": checks, "thresholds": GATE}


# ------------------------------------------------------------- comparison


def per_sample_log_loss(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.clip(P[np.arange(len(y)), y], EPS, 1))


def block_bootstrap_diff(a: np.ndarray, b: np.ndarray, block: int, n_boot: int = 1000,
                         seed: int = 0, alpha: float = 0.05) -> dict[str, Any]:
    """Paired moving-block bootstrap of mean(a − b) for per-sample losses.

    Blocks of consecutive predictions keep the serial dependence of
    overlapping forecast horizons. "better" means the whole (1 − alpha) CI of
    a − b lies below zero, i.e. `a` has lower loss than `b` beyond resampling
    noise.
    """
    d = np.asarray(a, float) - np.asarray(b, float)
    n = len(d)
    block = int(max(1, min(block, n)))
    rng = np.random.default_rng(seed)
    k = int(math.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, k))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    means = d[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return {"mean_diff": round(float(d.mean()), 5), "ci_low": round(float(lo), 5),
            "ci_high": round(float(hi), 5), "n": int(n), "block": block, "n_boot": n_boot,
            "confidence": 1 - alpha, "better": bool(hi < 0)}
