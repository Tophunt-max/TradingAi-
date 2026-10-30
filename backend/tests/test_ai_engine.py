"""Multi-model AI engine (work package K)."""

from __future__ import annotations

import uuid

import numpy as np
import pytest
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegression
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.ai import engine as E
from app.ai import features as F
from app.ai import model as M
from app.analysis import context as C
from app.models import ModelVersion, User, UserRole
from tests.conftest import auth_header, register_and_verify
from tests.test_phase4_analysis import trend_bars

KEY = b"test-key"


def _data(n: int = 1600, d: int = 4, seed: int = 0, k: int = 3) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, d))
    if k == 3:
        logits = np.stack([-1.5 * X[:, 0], np.zeros(n), 1.5 * X[:, 0]], axis=1)
    else:
        logits = np.stack([np.zeros(n), 2 * X[:, 0]], axis=1)
    p = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    y = np.array([rng.choice(k, p=row) for row in p])
    return X, y


# ------------------------------------------------------------------- labels


def test_three_class_and_binary_labels():
    up = C.build(trend_bars(80, 1.0, noise=0.0))
    flat = C.build(trend_bars(80, 0.0, noise=0.0))
    down = C.build(trend_bars(80, -1.0, noise=0.0, start=500))
    t, h = 60, 4
    assert F.label(up, t, h) == 2 and F.label(down, t, h) == 0 and F.label(flat, t, h) == 1
    assert F.label_binary(up, t, h) == 1 and F.label_binary(down, t, h) == 0
    assert F.label_binary(flat, t, h) is None  # exact tie dropped
    assert F.label(up, 78, h) is None and F.label_binary(up, 78, h) is None
    assert F.classes_for("binary") == ["DOWN", "UP"]
    _, yb, _ = F.dataset(up, h, "binary")
    assert set(yb) == {1}
    with pytest.raises(ValueError):
        F.dataset(up, h, "nope")


def test_binary_logistic_params_reproduce_sklearn_sigmoid():
    X, y = _data(k=2)
    p = M.fit(X, y, n_classes=2)
    mean, scale = np.array(p.mean), np.array(p.scale)
    clf = LogisticRegression(C=M.HYPERPARAMS["C"], max_iter=M.HYPERPARAMS["max_iter"],
                             random_state=0).fit((X - mean) / scale, y)
    np.testing.assert_allclose(M.predict_proba(p, X[:30]),
                               clf.predict_proba((X[:30] - mean) / scale), atol=1e-9)


# ------------------------------------------------------- determinism & leakage


@pytest.mark.parametrize("mt", ["random_forest", "hist_gb"])
def test_tree_training_is_deterministic_with_seed(mt):
    X, y = _data(1200)
    a = E.train(X, y, horizon=3, model_type=mt, seed=5, key=KEY)
    b = E.train(X, y, horizon=3, model_type=mt, seed=5, key=KEY)
    np.testing.assert_array_equal(a["wf"]["P"], b["wf"]["P"])
    assert a["metrics"]["log_loss"] == b["metrics"]["log_loss"]
    x = X[-1].tolist()
    pa = E.predict(a["params"], x, key=KEY, horizon=3, rv20=None, close=100.0)
    pb = E.predict(b["params"], x, key=KEY, horizon=3, rv20=None, close=100.0)
    assert pa["probabilities"] == pb["probabilities"]


def test_walk_forward_trains_only_on_rows_before_the_purge_gap():
    n, horizon = 1000, 6
    X = np.arange(n, dtype=float)[:, None]  # feature = row index
    y = np.arange(n) % 3
    seen: list[tuple[float, int]] = []

    def fp(Xtr, ytr, Xte, k):
        seen.append((Xtr[:, 0].max(), int(Xte[0, 0])))
        return np.full((len(Xte), 3), 1 / 3)

    wf = M.walk_forward(X, y, horizon, fit_predict=fp)
    assert seen and len(seen) == len(wf["folds"])
    for (max_train, first_test), f in zip(seen, wf["folds"], strict=True):
        assert max_train < first_test - horizon + 1  # ≥ horizon rows purged
        assert first_test == f["test_start"] and f["train_rows"] == f["test_start"] - horizon


def test_tree_calibration_split_is_chronological_with_purge():
    X, y = _data(1500)
    f = E.RandomForestSpec().fit(X, y, seed=0, n_classes=3, horizon=4)
    cal = f.calibration
    assert cal["status"] == "CALIBRATED_ISOTONIC" or cal["status"] == "CALIBRATED_SIGMOID"
    assert cal["fit_rows"] + cal["purge_rows"] + cal["calibration_rows"] == 1500


# ------------------------------------------------------------------ calibration


class Overconfident(ClassifierMixin, BaseEstimator):
    """A logistic model whose logits are multiplied by 4 (miscalibrated on purpose)."""

    def fit(self, X, y):
        self.lr_ = LogisticRegression(max_iter=1000).fit(X, y)
        self.classes_ = self.lr_.classes_
        return self

    def predict_proba(self, X):
        z = 4 * self.lr_.decision_function(X)
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def predict(self, X):
        return self.classes_[self.predict_proba(X).argmax(axis=1)]


class OverconfidentSpec(E.TreeSpec):
    model_type = "overconfident"

    def build(self, seed):
        return Overconfident()


def test_calibration_reduces_ece_on_a_miscalibrated_model():
    X, y = _data(6000, seed=7)
    Xtr, ytr, Xte, yte = X[:4000], y[:4000], X[4000:], y[4000:]
    raw = Overconfident().fit(Xtr, ytr)
    f = OverconfidentSpec().fit(Xtr, ytr, seed=0, n_classes=3, horizon=2)
    assert f.calibration["status"].startswith("CALIBRATED")
    ece_raw = M.calibration(raw.predict_proba(Xte), yte)[0]
    ece_cal = M.calibration(OverconfidentSpec().predict_proba(f, Xte), yte)[0]
    assert ece_raw > 0.08 and ece_cal < ece_raw / 2


# ------------------------------------------------------------------------- OOD


def test_ood_flags_shifted_inputs():
    X, _ = _data(2000, d=5)
    st = E.ood_stats(X, [f"x{j}" for j in range(5)])
    assert E.ood_check(st, X[10])["status"] == "IN_DISTRIBUTION"
    shifted = X[10] + 8.0
    out = E.ood_check(st, shifted)
    assert out["status"] == "OUT_OF_DISTRIBUTION"
    assert out["mahalanobis_d2"] > out["threshold"]
    assert set(out["features_outside_training_range"]) == {f"x{j}" for j in range(5)}
    assert E.ood_check(None, shifted)["status"] == "NOT_AVAILABLE"
    in_rate = np.mean([E.ood_check(st, x)["status"] == "OUT_OF_DISTRIBUTION" for x in X[:500]])
    assert in_rate < 0.02


# ------------------------------------------------------------------ comparison


def test_block_bootstrap_ci_logic():
    rng = np.random.default_rng(0)
    base = rng.normal(1.0, 0.3, size=800)
    better = M.block_bootstrap_diff(base - 0.1, base, block=10, seed=1)
    assert better["better"] and better["ci_high"] < 0
    same = M.block_bootstrap_diff(base + rng.normal(0, 0.3, 800), base, block=10, seed=1)
    assert same["ci_low"] < 0 < same["ci_high"] and not same["better"]
    worse = M.block_bootstrap_diff(base + 0.1, base, block=10, seed=1)
    assert not worse["better"] and worse["ci_low"] > 0
    assert M.block_bootstrap_diff(base, base * 1.01, 10, seed=3) == \
        M.block_bootstrap_diff(base, base * 1.01, 10, seed=3)


def test_challenger_gate_requires_beating_logistic_and_demo_never_passes():
    X, y = _data(2000)
    res = E.train(X, y, horizon=2, model_type="random_forest", key=KEY)
    m = res["metrics"]
    assert {"vs_logistic", "vs_climatology", "logistic_baseline", "sample_sizes"} <= set(m)
    assert m["sample_sizes"]["oos_predictions"] == m["oos_samples"]
    g = M.gate(m, "LIVE")
    check = next(c for c in g["checks"] if c["key"] == "beats_logistic")
    assert check["passed"] == m["vs_logistic"]["better"]
    # The data truly is logistic, so a forest should not beat the logistic baseline.
    assert m["vs_logistic"]["better"] is False and g["passed"] is False
    forced = {**m, "vs_logistic": {**m["vs_logistic"], "better": True}, "ece": 0.0,
              "log_loss": 0.1}
    assert M.gate(forced, "DEMO")["passed"] is False
    lr = E.train(X, y, horizon=2, model_type="logistic", key=KEY)["metrics"]
    assert "vs_logistic" not in lr and lr["vs_climatology"]["better"]


# ---------------------------------------------------------------- serialization


@pytest.mark.parametrize("spec", [E.RandomForestSpec(), E.HistGBSpec()])
def test_tree_models_round_trip_and_reject_tampering(spec):
    X, y = _data(900)
    f = spec.fit(X, y, seed=0, n_classes=3, horizon=2)
    d = spec.dump(f, KEY)
    assert d["artifact"] and d["bytes"] < E.MAX_ARTIFACT_B64 and d["hmac_sha256"]
    g = spec.load(d, KEY)
    np.testing.assert_array_equal(spec.predict_proba(f, X[:50]), spec.predict_proba(g, X[:50]))
    with pytest.raises(E.ArtifactError):
        spec.load(d, b"other-key")
    bad = dict(d, artifact=d["artifact"][:-8] + "AAAAAAA=")
    with pytest.raises(E.ArtifactError):
        spec.load(bad, KEY)
    with pytest.raises(E.ArtifactError):
        spec.load(dict(d, sklearn_version="0.0"), KEY)
    with pytest.raises(E.ArtifactError):
        spec.load({"artifact": None, "reason": "too large"}, KEY)


def test_prediction_carries_distribution_uncertainty_and_explanation():
    X, y = _data(1500, d=len(F.FEATURE_NAMES))
    ret = list(np.where(y == 2, 0.5, np.where(y == 0, -0.5, 0.0)) + 0.01 * np.arange(len(y))
               % 0.1)
    res = E.train(X, y, horizon=2, model_type="logistic", key=KEY, ret_pct=ret,
                  vol_ratio=[1.0 + 0.001 * i % 0.5 for i in range(len(y))])
    out = E.predict(res["params"], X[-1].tolist(), key=KEY, horizon=2, rv20=0.01, close=100.0)
    assert abs(sum(out["probabilities"].values()) - 1) < 1e-3
    assert 0 <= out["uncertainty"] <= 1
    assert out["uncertainty_detail"]["ood"]["status"] == "IN_DISTRIBUTION"
    assert out["calibration_status"] == "UNCALIBRATED"
    assert out["explanation"]["scope"] == "LOCAL" and len(out["explanation"]["top_features"]) == 5
    er = out["expected_return_pct"]
    assert er["p05"] <= er["p50"] <= er["p95"]
    vf = out["volatility_forecast"]
    assert vf["q10"]["per_bar_vol"] <= vf["q50"]["per_bar_vol"] <= vf["q90"]["per_bar_vol"]


# ------------------------------------------------------------------------- API


@pytest.fixture
def admin(client, otp, engine):
    tokens = register_and_verify(client, otp, "admin@example.com")
    with Session(engine) as db:
        db.execute(update(User).where(User.email == "admin@example.com")
                   .values(role=UserRole.ADMIN))
        db.commit()
    return auth_header(tokens)


def test_train_tree_model_on_demo_is_rejected_compared_and_serves(client, admin, engine):
    r = client.post("/api/v1/admin/models/train", headers=admin,
                    json={"symbol": "NIFTY", "timeframe": "15m", "model_type": "hist_gb",
                          "target": "three_class", "seed": 1})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["algorithm"] == "histgb-v1" and m["status"] == "REJECTED"
    assert m["metrics"]["model_type"] == "hist_gb" and "vs_logistic" in m["metrics"]
    assert any(c["key"] == "beats_logistic" for c in m["gate"]["checks"])
    assert client.post(f"/api/v1/admin/models/{m['id']}/promote",
                       headers=admin).status_code == 409
    lr = client.post("/api/v1/admin/models/train", headers=admin,
                     json={"symbol": "NIFTY", "timeframe": "15m", "target": "binary"}).json()
    assert lr["metrics"]["target"] == "binary"
    cmp_ = client.get("/api/v1/models/compare?symbol=NIFTY&timeframe=15m", headers=admin)
    assert cmp_.status_code == 200, cmp_.text
    rows = cmp_.json()["rows"]
    assert {x["model_type"] for x in rows} == {"hist_gb", "logistic"}
    assert all(x["promotable"] is False and x["data_source"] == "DEMO" for x in rows)
    hg = next(x for x in rows if x["model_type"] == "hist_gb")
    assert hg["vs_logistic"]["n"] == hg["oos_samples"]
    assert hg["calibration_status"].startswith("CALIBRATED")
    # Serving path (set PROMOTED directly; the API refuses DEMO promotion).
    with Session(engine) as db:
        db.execute(update(ModelVersion).where(ModelVersion.id == uuid.UUID(m["id"]))
                   .values(status="PROMOTED"))
        db.commit()
    b = client.get("/api/v1/analysis/NIFTY?timeframe=15m", headers=admin).json()
    p = b["prediction"]
    assert p["probabilities"] and p["model_type"] == "hist_gb"
    for k in ("model_version", "feature_version", "input_quality", "regime",
              "calibration_status", "explanation", "uncertainty_detail", "order_policy"):
        assert p[k] is not None, k
    assert b["decision"] == "NO_TRADE"
    bad = client.post("/api/v1/admin/models/train", headers=admin,
                      json={"symbol": "NIFTY", "model_type": "xgboost"})
    assert bad.status_code == 422
