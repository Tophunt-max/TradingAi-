"""Drift monitoring: feature PSI, prediction PSI and performance drift.

PSI = Σ (a_i − e_i) · ln(a_i / e_i) over bins defined by the reference (training)
deciles; empty bins are floored at 1e-4. Thresholds: < 0.10 stable, 0.10–0.25
moderate, > 0.25 significant. Significant drift on any feature or on the predicted
class mix, or a rolling Brier score more than 15 % worse than validation over at
least 100 resolved predictions, marks the model DEGRADED and the serving path abstains.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.learning import stats as ST
from app.models.intelligence import ModelDriftReport
from app.models.ml import ModelVersion, Prediction

PSI_MODERATE = 0.10
PSI_SIGNIFICANT = 0.25
PSI_FLOOR = 1e-4
QUANTILES = 10
MIN_WINDOW_ROWS = 100
PERF_MIN_SAMPLES = 100
PERF_WINDOW = 300
PERF_MAX_RATIO = 1.15
RECENT_ROWS = 300


def psi_level(v: float | None) -> str:
    if v is None or math.isnan(v):
        return "UNKNOWN"
    return "SIGNIFICANT" if v > PSI_SIGNIFICANT else "MODERATE" if v >= PSI_MODERATE \
        else "STABLE"


def reference_column(x: np.ndarray) -> dict[str, Any]:
    """Interior decile edges and the reference share of each bin."""
    edges = np.unique(np.quantile(x, np.linspace(0, 1, QUANTILES + 1)[1:-1]))
    counts = np.bincount(np.searchsorted(edges, x, side="right"), minlength=len(edges) + 1)
    return {"edges": [round(float(e), 8) for e in edges],
            "fractions": [round(float(c) / len(x), 6) for c in counts]}


def reference_from_matrix(X: np.ndarray, names: list[str]) -> dict[str, Any]:
    return {"rows": int(len(X)),
            "features": {n: reference_column(X[:, i]) for i, n in enumerate(names)}}


def psi(ref: dict[str, Any], x: np.ndarray) -> float:
    edges = np.array(ref["edges"])
    e = np.maximum(np.array(ref["fractions"], dtype=float), PSI_FLOOR)
    counts = np.bincount(np.searchsorted(edges, x, side="right"), minlength=len(edges) + 1)
    a = np.maximum(counts / max(len(x), 1), PSI_FLOOR)
    return round(float(np.sum((a - e) * np.log(a / e))), 5)


def categorical_psi(expected: dict[str, float], actual: dict[str, float]) -> float:
    keys = set(expected) | set(actual)
    tot = 0.0
    for k in keys:
        e, a = max(expected.get(k, 0.0), PSI_FLOOR), max(actual.get(k, 0.0), PSI_FLOOR)
        tot += (a - e) * math.log(a / e)
    return round(tot, 5)


def feature_drift(reference: dict[str, Any], X: np.ndarray, names: list[str]
                  ) -> dict[str, dict[str, Any]]:
    out = {}
    for i, n in enumerate(names):
        ref = reference["features"].get(n)
        if ref is None:
            continue
        v = psi(ref, X[:, i])
        out[n] = {"psi": v, "level": psi_level(v)}
    return out


def performance_drift(briers: list[float], validation_brier: float | None) -> dict[str, Any]:
    n = len(briers)
    if n < PERF_MIN_SAMPLES or validation_brier is None:
        return {"status": "INSUFFICIENT_DATA", "samples": n, "min_samples": PERF_MIN_SAMPLES,
                "rolling_brier": round(float(np.mean(briers)), 5) if n else None,
                "validation_brier": validation_brier,
                "note": "Fewer resolved predictions than the minimum: no conclusion is drawn "
                        "(a few losses never demote a model)."}
    rb = float(np.mean(briers))
    ratio = rb / validation_brier if validation_brier else math.inf
    return {"status": "DEGRADED" if ratio > PERF_MAX_RATIO else "STABLE", "samples": n,
            "min_samples": PERF_MIN_SAMPLES, "rolling_brier": round(rb, 5),
            "validation_brier": validation_brier, "ratio": round(ratio, 4),
            "max_ratio": PERF_MAX_RATIO}


def combine(feature: dict[str, dict[str, Any]], prediction_psi: float | None,
            perf: dict[str, Any], window_rows: int) -> tuple[str, list[str], float | None]:
    reasons = []
    mx = max((v["psi"] for v in feature.values()), default=None)
    sig = [n for n, v in feature.items() if v["level"] == "SIGNIFICANT"]
    mod = [n for n, v in feature.items() if v["level"] == "MODERATE"]
    status = "STABLE"
    if window_rows < MIN_WINDOW_ROWS and perf["status"] == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA", [f"Only {window_rows} recent feature rows "
                                     f"(need {MIN_WINDOW_ROWS})."], mx
    if sig:
        status = "DEGRADED"
        reasons.append(f"Significant feature drift (PSI > {PSI_SIGNIFICANT}): "
                       + ", ".join(f"{n} {feature[n]['psi']:.2f}" for n in sig) + ".")
    if prediction_psi is not None and prediction_psi > PSI_SIGNIFICANT:
        status = "DEGRADED"
        reasons.append(f"Predicted class mix drifted (PSI {prediction_psi:.2f}).")
    if perf["status"] == "DEGRADED":
        status = "DEGRADED"
        reasons.append(f"Rolling Brier {perf['rolling_brier']} is {perf['ratio']:.2f}× the "
                       f"validation Brier over {perf['samples']} resolved predictions.")
    if status == "STABLE" and (mod or (prediction_psi or 0) >= PSI_MODERATE):
        status = "MODERATE"
        reasons.append("Moderate drift: " + ", ".join(mod or ["predicted class mix"]) + ".")
    if status == "STABLE":
        reasons.append("No drift above thresholds.")
    return status, reasons, mx


def _recompute_reference(provider: Any, mv: ModelVersion) -> tuple[dict | None, str]:
    """Rebuild the training-window feature distribution from candles (fallback).

    Used for models trained before train-time references were stored. Only rows
    whose timestamp lies in the recorded training window are used; if the provider
    no longer has at least half of them, the reference is unavailable.
    """
    from app.ai import features as F
    from app.analysis import context as C

    r = provider.candles(mv.symbol, mv.timeframe, 3000)
    ctx = C.build(r.data, intraday=mv.timeframe != "1d")
    rows = [F.row(ctx, t) for t in range(len(ctx)) if mv.data_from <= ctx.ts[t] <= mv.data_to]
    rows = [x for x in rows if x is not None]
    if len(rows) < max(MIN_WINDOW_ROWS, 0.5 * mv.train_rows):
        return None, "UNAVAILABLE"
    return reference_from_matrix(np.array(rows), F.FEATURE_NAMES), "RECOMPUTED_TRAINING_WINDOW"


def recent_features(provider: Any, mv: ModelVersion) -> tuple[np.ndarray, str]:
    from app.ai import features as F
    from app.analysis import context as C

    r = provider.candles(mv.symbol, mv.timeframe, RECENT_ROWS + 60)
    ctx = C.build(r.data, intraday=mv.timeframe != "1d")
    rows = [F.row(ctx, t) for t in range(max(0, len(ctx) - RECENT_ROWS), len(ctx))]
    rows = [x for x in rows if x is not None]
    return (np.array(rows) if rows else np.zeros((0, len(F.FEATURE_NAMES)))), r.source


def compute_report(db: Session, provider: Any, mv: ModelVersion, now: datetime
                   ) -> ModelDriftReport:
    from app.ai import features as F

    ref = mv.metrics.get("feature_reference")
    basis = "TRAIN_TIME" if ref else ""
    if not ref:
        ref, basis = _recompute_reference(provider, mv)
    X, _ = recent_features(provider, mv)
    feat = feature_drift(ref, X, F.FEATURE_NAMES) if ref and len(X) else {}
    preds = db.scalars(select(Prediction).where(Prediction.model_version_id == mv.id)
                       .order_by(Prediction.bar_ts.desc()).limit(PERF_WINDOW)).all()
    pred_psi = None
    exp_share = mv.metrics.get("oos_predicted_share")
    classes = ST.classes_of(mv)
    if exp_share and len(preds) >= MIN_WINDOW_ROWS:
        top = [classes[i] for i in ST.prediction_matrix(list(preds), classes).argmax(axis=1)]
        act = {c: top.count(c) / len(top) for c in classes}
        pred_psi = categorical_psi(exp_share, act)
    resolved = [p for p in preds if p.outcome in classes]
    briers: list[float] = []
    if resolved:
        P = ST.prediction_matrix(resolved, classes)
        y = np.array([classes.index(p.outcome) for p in resolved])  # type: ignore[arg-type]
        briers = [float(b) for b in ST.per_sample_brier(P, y)]
    perf = performance_drift(briers, mv.metrics.get("brier"))
    status, reasons, mx = combine(feat, pred_psi, perf, len(X))
    if not ref:
        reasons.insert(0, "No training feature reference: the training window is no longer "
                          "available from the provider, so feature drift cannot be measured.")
    rep = ModelDriftReport(model_version_id=mv.id, computed_at=now, status=status,
                           reference_basis=basis or "UNAVAILABLE", window_rows=int(len(X)),
                           max_feature_psi=mx, feature_psi=feat, prediction_psi=pred_psi,
                           performance=perf, reasons=reasons)
    db.add(rep)
    db.flush()
    return rep


def latest(db: Session, model_id: uuid.UUID) -> ModelDriftReport | None:
    return db.scalar(select(ModelDriftReport).where(ModelDriftReport.model_version_id == model_id)
                     .order_by(ModelDriftReport.computed_at.desc()).limit(1))


def due(db: Session, model_id: uuid.UUID, now: datetime, every: timedelta) -> bool:
    last = latest(db, model_id)
    return last is None or last.computed_at <= now - every
