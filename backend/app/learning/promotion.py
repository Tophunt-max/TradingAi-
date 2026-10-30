"""Promotion policy: CANDIDATE → SHADOW → PROMOTED → RETIRED / ROLLED_BACK.

Acceptance criteria (all enforced here, documented in docs/learning.md):

Offline (to enter SHADOW, re-checked before PROMOTED):
  O1 the training gate passed (app.ai.model.gate);
  O2 at least 500 out-of-sample predictions;
  O3 log-loss and Brier improvement over climatology, block-bootstrap 95 % CI lower
     bound > 0 (CI excludes zero);
  O4 expected calibration error ≤ 0.05;
  O5 trained on non-DEMO data and the current feature version.

Shadow / paper validation (to become PROMOTED):
  S1 status SHADOW; predictions were recorded live but never shown or used;
  S2 at least 200 resolved shadow predictions, all on non-DEMO data;
  S3 beats the training climatology on log loss and Brier on those outcomes with a
     block-bootstrap 95 % CI excluding zero;
  S4 ECE on shadow outcomes ≤ 0.05.

Nothing changes automatically. A model is never demoted or promoted on a handful
of outcomes: every rule has a minimum sample size.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.time import utcnow
from app.learning import stats as ST
from app.models.ml import ModelVersion, Prediction

POLICY = {
    "offline_min_oos_samples": 500,
    "offline_max_ece": 0.05,
    "shadow_min_resolved": 200,
    "shadow_max_ece": 0.05,
    "bootstrap_alpha": 0.05,
}


def _check(key: str, label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "passed": bool(ok), "detail": detail}


def _ci_ok(b: dict[str, Any] | None, k: str) -> tuple[bool, str]:
    if not b or k not in b:
        return False, "Not available: retrain to compute the bootstrap interval."
    ci = b[k]
    return ci["lo"] > 0, (f"mean {ci['mean']:+.4f}, 95% CI [{ci['lo']:+.4f}, {ci['hi']:+.4f}] "
                          f"(n={b['n']}, block {b['block']}); lower bound must be > 0.")


def offline_checks(mv: ModelVersion) -> list[dict[str, Any]]:
    from app.ai import features as F

    m = mv.metrics
    out = [_check("gate", "Training gate passed", bool(mv.gate.get("passed")),
                  "All training-gate checks passed." if mv.gate.get("passed")
                  else "The training gate failed.")]
    n = int(m.get("oos_samples", 0))
    out.append(_check("oos_samples", "Out-of-sample sample size",
                      n >= POLICY["offline_min_oos_samples"],
                      f"{n} (need ≥ {POLICY['offline_min_oos_samples']})."))
    for k, label in (("log_loss_improvement", "Beats climatology on log loss (CI)"),
                     ("brier_improvement", "Beats climatology on Brier (CI)")):
        ok, detail = _ci_ok(m.get("bootstrap"), k)
        out.append(_check(f"offline_{k}", label, ok, detail))
    ece = m.get("ece")
    out.append(_check("offline_ece", "Calibrated out of sample",
                      ece is not None and ece <= POLICY["offline_max_ece"],
                      f"ECE {ece} (need ≤ {POLICY['offline_max_ece']})."))
    out.append(_check("data", "Real (non-DEMO) training data", mv.data_source != "DEMO",
                      f"Source {mv.data_source}."))
    out.append(_check("features", "Current feature version",
                      mv.feature_version == F.FEATURE_VERSION,
                      f"{mv.feature_version} (current {F.FEATURE_VERSION})."))
    return out


def shadow_predictions(db: Session, mv: ModelVersion) -> list[Prediction]:
    stmt = select(Prediction).where(Prediction.model_version_id == mv.id,
                                    Prediction.outcome.is_not(None))
    if mv.shadow_started_at is not None:
        stmt = stmt.where(Prediction.created_at >= mv.shadow_started_at)
    return list(db.scalars(stmt.order_by(Prediction.bar_ts)).all())


def shadow_evaluation(mv: ModelVersion, preds: list[Prediction]) -> dict[str, Any]:
    """Score resolved shadow predictions against the training climatology."""
    classes = ST.classes_of(mv)
    rows = [p for p in preds if p.outcome in classes]
    checks = [_check("shadow_status", "Validated in SHADOW", mv.status == "SHADOW",
                     f"Status {mv.status}.")]
    n = len(rows)
    checks.append(_check("shadow_samples", "Resolved shadow predictions",
                         n >= POLICY["shadow_min_resolved"],
                         f"{n} (need ≥ {POLICY['shadow_min_resolved']})."))
    demo = sum(1 for p in rows if p.data_source == "DEMO")
    checks.append(_check("shadow_data", "Shadow outcomes on real data", n > 0 and demo == 0,
                         f"{demo} of {n} on DEMO data."))
    metrics: dict[str, Any] = {"samples": n}
    share = mv.metrics.get("class_share") or {}
    if n and share:
        P = ST.prediction_matrix(rows, classes)
        y = np.array([classes.index(p.outcome) for p in rows])  # type: ignore[arg-type]
        B = np.tile([share.get(c, 0.0) for c in classes], (n, 1))
        boot = ST.improvement_vs_baseline(P, B, y, block=max(1, mv.horizon_bars))
        ece, table = ST.calibration_table(P, y)
        metrics.update({"log_loss": round(float(ST.per_sample_log_loss(P, y).mean()), 5),
                        "baseline_log_loss": round(float(ST.per_sample_log_loss(B, y).mean()),
                                                   5),
                        "brier": round(float(ST.per_sample_brier(P, y).mean()), 5),
                        "baseline_brier": round(float(ST.per_sample_brier(B, y).mean()), 5),
                        "ece": ece, "calibration": table, "bootstrap": boot})
    else:
        boot, ece = None, None
    for k, label in (("log_loss_improvement", "Shadow beats climatology on log loss (CI)"),
                     ("brier_improvement", "Shadow beats climatology on Brier (CI)")):
        ok, detail = _ci_ok(boot, k)
        checks.append(_check(f"shadow_{k}", label, ok, detail))
    checks.append(_check("shadow_ece", "Calibrated on shadow outcomes",
                         ece is not None and ece <= POLICY["shadow_max_ece"],
                         f"ECE {ece} (need ≤ {POLICY['shadow_max_ece']})."))
    return {"passed": all(c["passed"] for c in checks), "checks": checks, "metrics": metrics}


def status(db: Session, mv: ModelVersion) -> dict[str, Any]:
    off = offline_checks(mv)
    sh = shadow_evaluation(mv, shadow_predictions(db, mv)) if mv.status in (
        "SHADOW", "PROMOTED") else None
    return {"model_id": str(mv.id), "status": mv.status, "policy": POLICY,
            "offline": {"passed": all(c["passed"] for c in off), "checks": off},
            "shadow": sh}


def to_shadow(db: Session, mv: ModelVersion, now: datetime | None = None) -> ModelVersion:
    if mv.status != "CANDIDATE":
        raise ConflictError(f"Only CANDIDATE models can enter SHADOW (status {mv.status}).",
                            code="MODEL_STATUS_INVALID")
    off = offline_checks(mv)
    if not all(c["passed"] for c in off):
        raise ConflictError("This model did not pass the offline promotion criteria.",
                            code="MODEL_GATE_FAILED", details=off)
    mv.status, mv.shadow_started_at = "SHADOW", now or utcnow()
    return mv


def promote(db: Session, mv: ModelVersion) -> ModelVersion:
    from app.ai import registry

    off = offline_checks(mv)
    if not all(c["passed"] for c in off):
        raise ConflictError("This model did not pass the promotion gate.",
                            code="MODEL_GATE_FAILED", details=off)
    if mv.status != "SHADOW":
        raise ConflictError("A model must pass the SHADOW (paper validation) stage before "
                            "promotion.", code="MODEL_SHADOW_REQUIRED")
    ev = shadow_evaluation(mv, shadow_predictions(db, mv))
    if not ev["passed"]:
        raise ConflictError("Shadow validation has not passed yet.", code="MODEL_SHADOW_FAILED",
                            details=ev["checks"])
    return registry.promote(db, mv)


def previous_promoted(db: Session, mv: ModelVersion) -> ModelVersion | None:
    return db.scalar(select(ModelVersion).where(
        ModelVersion.symbol == mv.symbol, ModelVersion.timeframe == mv.timeframe,
        ModelVersion.horizon_bars == mv.horizon_bars, ModelVersion.id != mv.id,
        ModelVersion.status == "RETIRED", ModelVersion.promoted_at.is_not(None))
        .order_by(ModelVersion.promoted_at.desc()).limit(1))


def rollback(db: Session, mv: ModelVersion, now: datetime | None = None
             ) -> tuple[ModelVersion, ModelVersion]:
    """Replace the PROMOTED `mv` with the previously promoted version."""
    from app.ai import features as F

    if mv.status != "PROMOTED":
        raise ConflictError("Only the PROMOTED model can be rolled back.",
                            code="MODEL_STATUS_INVALID")
    prev = previous_promoted(db, mv)
    if prev is None:
        raise ConflictError("No previously promoted version to roll back to. Retire the "
                            "model instead.", code="NO_PREVIOUS_MODEL")
    if prev.feature_version != F.FEATURE_VERSION or prev.data_source == "DEMO":
        raise ConflictError("The previous version cannot serve (outdated features or DEMO "
                            "data).", code="PREVIOUS_MODEL_INVALID")
    now = now or utcnow()
    mv.status, mv.rolled_back_at, mv.retired_at = "ROLLED_BACK", now, now
    prev.status, prev.promoted_at, prev.retired_at = "PROMOTED", now, None
    return mv, prev
