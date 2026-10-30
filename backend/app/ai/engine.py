"""Multi-model engine: logistic baseline, random forest, histogram gradient boosting.

All models share one interface (ModelSpec) and one evaluation protocol:
purged expanding-window walk-forward (app.ai.model.walk_forward), metrics vs
climatology and vs the logistic baseline with a moving-block bootstrap CI,
plus OOD statistics, an expected-return distribution and a volatility-range
forecast. Nothing here promotes a model or places an order.

Persistence (no new tables): everything is stored in the JSON `params` column
of model_versions. The logistic model is plain JSON. Tree models are stored as
a zlib-compressed pickle, base64 encoded, with an HMAC-SHA256 tag keyed by the
server SECRET_KEY and the scikit-learn version. Loading verifies the tag and
the version BEFORE unpickling, so only artifacts written by this server are
ever unpickled; user-supplied data is never unpickled. Artifacts above
MAX_ARTIFACT_B64 are not stored; such a model abstains at serving time and
must be retrained from its recorded recipe (symbol, timeframe, window, seed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import pickle  # noqa: S403 - only HMAC-verified, self-produced artifacts are loaded
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sklearn
from scipy.stats import chi2
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.frozen import FrozenEstimator

from app.ai import features as F
from app.ai import model as M

MODEL_TYPES = ("logistic", "random_forest", "hist_gb")
MAX_ARTIFACT_B64 = 4_000_000
CAL_FRACTION = 0.2
MIN_CAL_ROWS = 100
ISOTONIC_MIN_ROWS = 1000
OOD_Z = 5.0
OOD_ALPHA = 0.001
QUANTILE_LEVELS = [round(x, 3) for x in np.linspace(0.025, 0.975, 39)]
BOOTSTRAP = 1000


class ArtifactError(Exception):
    pass


# ------------------------------------------------------------------ specs


@dataclass
class Fitted:
    """A fitted model plus how its probabilities were calibrated."""

    model: Any
    calibration: dict[str, Any] = field(default_factory=lambda: {"status": "UNCALIBRATED"})


class ModelSpec:
    model_type = "base"
    algorithm = "base"
    hyperparams: dict[str, Any] = {}

    def fit(self, X: np.ndarray, y: np.ndarray, *, seed: int, n_classes: int,
            horizon: int) -> Fitted:
        raise NotImplementedError

    def predict_proba(self, f: Fitted, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def dump(self, f: Fitted, key: bytes) -> dict[str, Any]:
        raise NotImplementedError

    def load(self, d: dict[str, Any], key: bytes) -> Fitted:
        raise NotImplementedError


class LogisticSpec(ModelSpec):
    """The existing JSON-parameter logistic regression (app.ai.model), unchanged."""

    model_type = "logistic"
    algorithm = M.ALGORITHM
    hyperparams = M.HYPERPARAMS

    def fit(self, X: np.ndarray, y: np.ndarray, *, seed: int, n_classes: int,
            horizon: int) -> Fitted:
        return Fitted(M.fit(X, y, seed=seed, n_classes=n_classes),
                      {"status": "UNCALIBRATED",
                       "note": "Raw multinomial logistic probabilities; out-of-sample ECE is "
                               "reported and gated."})

    def predict_proba(self, f: Fitted, X: np.ndarray) -> np.ndarray:
        return M.predict_proba(f.model, X)

    def dump(self, f: Fitted, key: bytes) -> dict[str, Any]:
        return f.model.as_dict()

    def load(self, d: dict[str, Any], key: bytes) -> Fitted:
        return Fitted(M.Params.from_dict(d))


class TreeSpec(ModelSpec):
    """Tree ensemble calibrated on a chronological hold-out of its training rows.

    Training rows [0, n − cal − horizon) fit the estimator; the last `cal` rows
    (after a purge gap of `horizon`) fit a Platt (sigmoid) or, with ≥ 1000 rows,
    isotonic calibrator on the frozen estimator. Never shuffled.
    """

    def build(self, seed: int) -> Any:
        raise NotImplementedError

    def fit(self, X: np.ndarray, y: np.ndarray, *, seed: int, n_classes: int,
            horizon: int) -> Fitted:
        n = len(y)
        cal = max(int(n * CAL_FRACTION), MIN_CAL_ROWS)
        fit_end = n - cal - horizon
        classes = set(range(n_classes))
        if (fit_end >= 200 and set(np.unique(y[:fit_end])) == classes
                and set(np.unique(y[n - cal:])) == classes):
            est = self.build(seed).fit(X[:fit_end], y[:fit_end])
            method = "isotonic" if cal >= ISOTONIC_MIN_ROWS else "sigmoid"
            cc = CalibratedClassifierCV(FrozenEstimator(est), method=method)
            cc.fit(X[n - cal:], y[n - cal:])
            return Fitted(cc, {"status": f"CALIBRATED_{method.upper()}", "method": method,
                               "fit_rows": int(fit_end), "calibration_rows": int(cal),
                               "purge_rows": int(horizon),
                               "note": "Calibrated on the most recent training rows "
                                       "(chronological hold-out)."})
        est = self.build(seed).fit(X, y)
        return Fitted(est, {"status": "UNCALIBRATED",
                            "note": "Too few rows or classes for a calibration hold-out."})

    def predict_proba(self, f: Fitted, X: np.ndarray) -> np.ndarray:
        return np.asarray(f.model.predict_proba(X), dtype=float)

    def dump(self, f: Fitted, key: bytes) -> dict[str, Any]:
        raw = zlib.compress(pickle.dumps(f.model, protocol=5), 9)
        b64 = base64.b64encode(raw).decode()
        meta = {"format": "pickle+zlib+base64", "sklearn_version": sklearn.__version__,
                "bytes": len(b64), "calibration": f.calibration}
        if len(b64) > MAX_ARTIFACT_B64:
            return {"artifact": None, **meta,
                    "reason": "Artifact too large to store; retrain from the recorded recipe."}
        tag = _tag(key, raw, sklearn.__version__)
        return {"artifact": b64, "hmac_sha256": tag, **meta}

    def load(self, d: dict[str, Any], key: bytes) -> Fitted:
        b64 = d.get("artifact")
        if not b64:
            raise ArtifactError(d.get("reason") or "No stored artifact.")
        if d.get("sklearn_version") != sklearn.__version__:
            raise ArtifactError(f"Artifact built with scikit-learn {d.get('sklearn_version')}; "
                                f"server runs {sklearn.__version__}. Retrain.")
        raw = base64.b64decode(b64)
        if not hmac.compare_digest(_tag(key, raw, d["sklearn_version"]), d.get("hmac_sha256",
                                                                               "")):
            raise ArtifactError("Artifact signature check failed; refusing to load.")
        return Fitted(pickle.loads(zlib.decompress(raw)),  # noqa: S301
                      d.get("calibration") or {})


def _tag(key: bytes, raw: bytes, version: str) -> str:
    return hmac.new(key, version.encode() + b"\0" + raw, hashlib.sha256).hexdigest()


class RandomForestSpec(TreeSpec):
    model_type = "random_forest"
    algorithm = "rf-v1"
    hyperparams = {"n_estimators": 100, "max_depth": 6, "min_samples_leaf": 25,
                   "max_features": "sqrt"}

    def build(self, seed: int) -> Any:
        return RandomForestClassifier(**self.hyperparams, random_state=seed, n_jobs=1)


class HistGBSpec(TreeSpec):
    """sklearn HistGradientBoostingClassifier: the LightGBM-style histogram boosting
    model (XGBoost/LightGBM are not installed)."""

    model_type = "hist_gb"
    algorithm = "histgb-v1"
    hyperparams = {"max_iter": 120, "learning_rate": 0.05, "max_leaf_nodes": 15,
                   "min_samples_leaf": 40, "l2_regularization": 1.0, "early_stopping": False}

    def build(self, seed: int) -> Any:
        return HistGradientBoostingClassifier(**self.hyperparams, random_state=seed)


SPECS: dict[str, ModelSpec] = {"logistic": LogisticSpec(), "random_forest": RandomForestSpec(),
                               "hist_gb": HistGBSpec()}


def spec_for(model_type: str) -> ModelSpec:
    if model_type not in SPECS:
        raise ValueError(f"Unknown model type {model_type!r}.")
    return SPECS[model_type]


# ------------------------------------------------------------------ OOD


def ood_stats(X: np.ndarray, names: list[str]) -> dict[str, Any]:
    """Training-distribution summary for out-of-distribution checks.

    Mahalanobis distance uses a shrunk covariance (10% towards its diagonal)
    so it is invertible; under a Gaussian approximation d² ~ χ²(features).
    """
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    cov = np.cov(X, rowvar=False)
    cov = 0.9 * cov + 0.1 * np.diag(np.diag(cov)) + 1e-9 * np.eye(len(mean))
    inv = np.linalg.pinv(cov)
    return {"names": names, "mean": mean.tolist(), "std": std.tolist(),
            "min": X.min(axis=0).tolist(), "max": X.max(axis=0).tolist(),
            "inv_cov": inv.tolist(), "threshold": float(chi2.ppf(1 - OOD_ALPHA, len(mean))),
            "z_limit": OOD_Z, "alpha": OOD_ALPHA}


def ood_check(stats: dict[str, Any] | None, x: np.ndarray) -> dict[str, Any]:
    if not stats:
        return {"status": "NOT_AVAILABLE", "reason": "No training-distribution summary stored."}
    mean, std = np.array(stats["mean"]), np.array(stats["std"])
    d = x - mean
    d2 = float(d @ np.array(stats["inv_cov"]) @ d)
    z = d / std
    lo, hi = np.array(stats["min"]), np.array(stats["max"])
    out_range = [stats["names"][j] for j in range(len(x)) if x[j] < lo[j] or x[j] > hi[j]]
    extreme = [stats["names"][j] for j in range(len(x)) if abs(z[j]) > stats["z_limit"]]
    ood = d2 > stats["threshold"] or bool(extreme)
    return {"status": "OUT_OF_DISTRIBUTION" if ood else "IN_DISTRIBUTION",
            "mahalanobis_d2": round(d2, 3), "threshold": round(stats["threshold"], 3),
            "features_beyond_z": extreme, "features_outside_training_range": out_range,
            "max_abs_z": round(float(np.abs(z).max()), 3)}


# -------------------------------------------------- return & volatility forecasts


def return_distribution(y: np.ndarray, ret_pct: list[float], n_classes: int) -> dict[str, Any]:
    """Class-conditional empirical forward-return quantiles (training rows)."""
    r = np.array(ret_pct, float)
    per = {}
    for c in range(n_classes):
        v = r[y == c]
        per[str(c)] = np.quantile(v, QUANTILE_LEVELS).round(5).tolist() if len(v) >= 20 else None
    return {"levels": QUANTILE_LEVELS, "per_class": per, "unit": "percent log return"}


def mixture_quantiles(dist: dict[str, Any] | None, p: np.ndarray,
                      at: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95)) -> dict | None:
    """Quantiles of Σ_c p_c · F_c, each F_c represented by its stored quantile grid."""
    if not dist:
        return None
    pts, wts = [], []
    for c, q in dist["per_class"].items():
        if q is None:
            return None
        pts.extend(q)
        wts.extend([p[int(c)] / len(q)] * len(q))
    order = np.argsort(pts)
    xs, cw = np.array(pts)[order], np.cumsum(np.array(wts)[order])
    cw /= cw[-1]
    out = {f"p{int(a * 100):02d}": round(float(xs[min(np.searchsorted(cw, a), len(xs) - 1)]),
                                         4) for a in at}
    out["mean"] = round(float(np.dot(np.array(pts), np.array(wts)) / sum(wts)), 4)
    return out


def vol_forecast_stats(ratio: list[float | None]) -> dict[str, Any] | None:
    v = np.array([x for x in ratio if x is not None and math.isfinite(x)], float)
    if len(v) < 50:
        return None
    q = np.quantile(v, [0.1, 0.5, 0.9])
    return {"ratio_q10": round(float(q[0]), 4), "ratio_q50": round(float(q[1]), 4),
            "ratio_q90": round(float(q[2]), 4), "samples": int(len(v)),
            "method": "Empirical ratio of forward per-bar realised vol to trailing rv20 per bar "
                      "(HV baseline scaled by training-period quantiles)."}


def vol_forecast(stats: dict[str, Any] | None, rv20: float | None, horizon: int,
                 close: float) -> dict[str, Any] | None:
    if not stats or not rv20:
        return None
    per_bar = rv20 / math.sqrt(20)
    out = {}
    for k in ("q10", "q50", "q90"):
        sig = per_bar * stats[f"ratio_{k}"]
        move = sig * math.sqrt(horizon)
        out[k] = {"per_bar_vol": round(sig, 6), "horizon_move_pct": round(move * 100, 4),
                  "range": [round(close * math.exp(-move), 2), round(close * math.exp(move), 2)]}
    return {**out, "horizon_bars": horizon,
            "note": "±1σ horizon range at the 10th/50th/90th percentile volatility forecast."}


# ------------------------------------------------------------- importance


def permutation_importance(spec: ModelSpec, f: Fitted, X: np.ndarray, y: np.ndarray,
                           names: list[str], seed: int = 0, repeats: int = 3) -> list[dict]:
    """Mean increase in log loss when one feature column is shuffled (held-out rows)."""
    rng = np.random.default_rng(seed)
    base = M.log_loss(spec.predict_proba(f, X), y)
    out = []
    for j, name in enumerate(names):
        inc = []
        for _ in range(repeats):
            Xp = X.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            inc.append(M.log_loss(spec.predict_proba(f, Xp), y) - base)
        out.append({"feature": name, "importance": round(float(np.mean(inc)), 5)})
    out.sort(key=lambda d: -d["importance"])
    return out


# ------------------------------------------------------------------ training


def _fit_predict(spec: ModelSpec, n_classes: int, horizon: int, seed: int) -> M.FitPredict:
    def fp(Xtr: np.ndarray, ytr: np.ndarray, Xte: np.ndarray, k: int) -> np.ndarray:
        f = spec.fit(Xtr, ytr, seed=seed + k, n_classes=n_classes, horizon=horizon)
        return spec.predict_proba(f, Xte)
    return fp


def train(X: np.ndarray, y: np.ndarray, *, horizon: int, model_type: str = "logistic",
          target: str = "three_class", seed: int = 0, ret_pct: list[float] | None = None,
          vol_ratio: list[float | None] | None = None, key: bytes = b"",
          regimes_fn: Any = None, folds: int = 5) -> dict[str, Any]:
    """Walk-forward evaluate, compare and fit one model type. Returns the pieces the
    registry stores: algorithm, hyperparams, params (JSON-safe), metrics."""
    spec = spec_for(model_type)
    names = (F.FEATURE_NAMES if X.shape[1] == len(F.FEATURE_NAMES)
             else [f"x{j}" for j in range(X.shape[1])])
    classes = F.classes_for(target)
    k = len(classes)
    wf = M.walk_forward(X, y, horizon, folds=folds,
                        fit_predict=_fit_predict(spec, k, horizon, seed), n_classes=k)
    regimes = regimes_fn(wf["test_idx"]) if regimes_fn else None
    metrics = M.evaluate(wf, regimes, classes=classes)
    yy = wf["y"]
    block = max(horizon, 10)
    ll = M.per_sample_log_loss(wf["P"], yy)
    metrics["vs_climatology"] = M.block_bootstrap_diff(
        ll, M.per_sample_log_loss(wf["B"], yy), block, BOOTSTRAP, seed)
    if model_type != "logistic":
        wf_lr = M.walk_forward(X, y, horizon, folds=folds, n_classes=k)
        if wf_lr["test_idx"] != wf["test_idx"]:
            raise ValueError("Baseline and challenger folds differ.")
        metrics["vs_logistic"] = M.block_bootstrap_diff(
            ll, M.per_sample_log_loss(wf_lr["P"], yy), block, BOOTSTRAP, seed)
        metrics["logistic_baseline"] = {"log_loss": round(M.log_loss(wf_lr["P"], yy), 5),
                                        "brier": round(M.brier(wf_lr["P"], yy), 5),
                                        "ece": round(M.calibration(wf_lr["P"], yy)[0], 5)}
    metrics["model_type"], metrics["target"] = model_type, target
    metrics["sample_sizes"] = {"labelled_rows": int(len(y)), "oos_predictions": int(len(yy)),
                               "folds": len(wf["folds"])}
    # Permutation importance: model fitted on the last fold's training rows, scored on
    # that fold's (out-of-sample) test rows.
    last = wf["folds"][-1]
    f_last = spec.fit(X[:last["train_rows"]], y[:last["train_rows"]], seed=seed,
                      n_classes=k, horizon=horizon)
    imp = permutation_importance(spec, f_last, X[last["test_start"]:last["test_end"]],
                                 y[last["test_start"]:last["test_end"]], names, seed=seed)
    fitted = spec.fit(X, y, seed=seed, n_classes=k, horizon=horizon)
    params: dict[str, Any] = {
        **spec.dump(fitted, key), "model_type": model_type, "target": target,
        "features": names, "classes": classes, "seed": seed,
        "calibration": fitted.calibration, "ood": ood_stats(X, names),
        "importance": imp[:10],
        "return_dist": return_distribution(y, ret_pct, k) if ret_pct is not None else None,
        "vol_forecast": vol_forecast_stats(vol_ratio) if vol_ratio is not None else None,
    }
    metrics["calibration_status"] = fitted.calibration["status"]
    return {"algorithm": spec.algorithm, "spec": spec, "wf": wf, "metrics": metrics,
            "params": params,
            "hyperparams": {**spec.hyperparams, "model_type": model_type, "target": target,
                            "seed": seed}}


# ------------------------------------------------------------------ serving


def model_type_of(params: dict[str, Any]) -> str:
    return params.get("model_type", "logistic")


def load(params: dict[str, Any], key: bytes) -> tuple[ModelSpec, Fitted]:
    spec = spec_for(model_type_of(params))
    return spec, spec.load(params, key)


def entropy(p: np.ndarray) -> float:
    k = len(p)
    return float(-sum(v * math.log(v) for v in p if v > 0) / math.log(k))


def explain(spec: ModelSpec, f: Fitted, params: dict[str, Any], x: np.ndarray,
            top: int) -> dict[str, Any]:
    if isinstance(spec, LogisticSpec):
        p: M.Params = f.model
        z = (x - np.array(p.mean)) / np.array(p.scale)
        coef = np.array(p.coef)
        rel = coef[top] - coef.mean(axis=0)
        names = params.get("features") or F.FEATURE_NAMES
        contrib = sorted(zip(names, (rel * z).tolist(), strict=True),
                         key=lambda kv: -abs(kv[1]))[:5]
        return {"method": "Logistic coefficient × standardised feature value for the most "
                          "likely class (relative to the class average).",
                "scope": "LOCAL",
                "top_features": [{"feature": n, "contribution": round(v, 4)}
                                 for n, v in contrib]}
    return {"method": "Permutation importance (increase in out-of-sample log loss when the "
                      "feature is shuffled).", "scope": "GLOBAL",
            "top_features": (params.get("importance") or [])[:5]}


def predict(params: dict[str, Any], x: list[float], *, key: bytes, horizon: int,
            rv20: float | None, close: float) -> dict[str, Any]:
    """Probabilities and context for one feature row. Raises ArtifactError if the
    stored artifact cannot be loaded safely."""
    spec, f = load(params, key)
    xa = np.array(x, float)
    p = spec.predict_proba(f, xa[None, :])[0]
    classes = params.get("classes") or F.CLASSES
    probs = {classes[i]: round(float(p[i]), 4) for i in range(len(classes))}
    top_i = int(np.argmax(p))
    ood = ood_check(params.get("ood"), xa)
    cal = params.get("calibration") or {"status": "UNCALIBRATED"}
    return {
        "probabilities": probs, "most_likely": classes[top_i], "confidence": probs[classes[top_i]],
        "uncertainty": round(entropy(p), 4),
        "uncertainty_detail": {"normalised_entropy": round(entropy(p), 4), "ood": ood},
        "calibration_status": cal.get("status", "UNCALIBRATED"),
        "expected_return_pct": mixture_quantiles(params.get("return_dist"), p),
        "volatility_forecast": vol_forecast(params.get("vol_forecast"), rv20, horizon, close),
        "explanation": explain(spec, f, params, xa, top_i),
        "model_type": model_type_of(params), "target": params.get("target", "three_class"),
    }
