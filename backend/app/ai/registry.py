"""Model registry: train candidates, gate-checked promotion, serving, predictions.

Nothing here retrains or promotes automatically. A human (admin) promotes a
candidate, and only if its out-of-sample gate passed on real data.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import engine as E
from app.ai import features as F
from app.ai import model as M
from app.analysis import context as C
from app.analysis import regime as R
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError
from app.core.time import utcnow
from app.learning.hooks import train_time_extras
from app.market_data.provider import MarketDataProvider
from app.models.ml import ModelVersion, Prediction

DEFAULT_HORIZON = {"1m": 15, "3m": 10, "5m": 6, "15m": 4, "30m": 4, "1h": 4, "1d": 3}
TRAIN_HISTORY = 3000


def _artifact_key() -> bytes:
    """HMAC key for stored tree-model artifacts (see app.ai.engine)."""
    return get_settings().secret_key.get_secret_value().encode()


def train_candidate(db: Session, provider: MarketDataProvider, *, symbol: str, timeframe: str,
                    horizon: int | None = None, history: int = TRAIN_HISTORY,
                    user_id: uuid.UUID | None = None, model_type: str = "logistic",
                    target: str = "three_class", seed: int = 0) -> ModelVersion:
    horizon = horizon or DEFAULT_HORIZON[timeframe]
    if model_type not in E.MODEL_TYPES or target not in F.TARGETS:
        raise AppError("Unknown model type or target.", code="VALIDATION_ERROR",
                       status_code=422)
    r = provider.candles(symbol, timeframe, history)
    if r.quality.status == "FAIL":
        raise AppError("Training data failed quality checks.", code="TRAINING_DATA_REJECTED",
                       status_code=422, details=r.quality.as_dict())
    ctx = C.build(r.data, intraday=timeframe != "1d")
    xs, ys, idx = F.dataset(ctx, horizon, target)
    if len(ys) < 400:
        raise AppError(f"Only {len(ys)} usable rows; need at least 400.",
                       code="INSUFFICIENT_DATA", status_code=422)
    X, y = np.array(xs), np.array(ys)

    def regimes_fn(test_idx: list[int]) -> list[str | None]:
        out = []
        for k in test_idx:
            reg = R.detect(ctx, idx[k])
            out.append(reg["label"] if reg else None)
        return out

    outc = F.outcomes(ctx, idx, horizon)
    res = E.train(X, y, horizon=horizon, model_type=model_type, target=target, seed=seed,
                  ret_pct=outc["ret_pct"], vol_ratio=outc["vol_ratio"], key=_artifact_key(),
                  regimes_fn=regimes_fn)
    metrics = res["metrics"]
    # Learning hook (app.learning): bootstrap CIs, drift reference, predicted-class mix.
    metrics.update(train_time_extras(X, res["wf"], horizon, F.FEATURE_NAMES,
                                     F.classes_for(target)))
    metrics["label_definition"] = F.label_definition(horizon, timeframe, target)
    metrics["recipe"] = {"symbol": symbol, "timeframe": timeframe, "history": history,
                         "horizon": horizon, "seed": seed, "model_type": model_type,
                         "target": target}
    g = M.gate(metrics, r.source)
    suffix = "" if model_type == "logistic" and target == "three_class" else \
        f" · {model_type}{' binary' if target == 'binary' else ''}"
    mv = ModelVersion(
        name=f"{symbol} {timeframe} +{horizon} bars{suffix}",
        symbol=symbol, timeframe=timeframe, horizon_bars=horizon, algorithm=res["algorithm"],
        feature_version=F.FEATURE_VERSION, hyperparams=res["hyperparams"],
        params=res["params"],
        metrics=metrics, gate=g, data_source=r.source, data_provider=r.provider,
        train_rows=len(ys), data_from=ctx.ts[idx[0]], data_to=ctx.ts[idx[-1]],
        status="CANDIDATE" if g["passed"] else "REJECTED", created_by=user_id,
    )
    db.add(mv)
    db.flush()
    return mv


def promote(db: Session, mv: ModelVersion) -> ModelVersion:
    if not mv.gate.get("passed") or mv.data_source == "DEMO":
        raise ConflictError("This model did not pass the promotion gate.",
                            code="MODEL_GATE_FAILED", details=mv.gate.get("checks"))
    if mv.feature_version != F.FEATURE_VERSION:
        raise ConflictError("Model was built with an older feature version.",
                            code="MODEL_FEATURES_OUTDATED")
    now = utcnow()
    db.execute(update(ModelVersion).where(
        ModelVersion.symbol == mv.symbol, ModelVersion.timeframe == mv.timeframe,
        ModelVersion.horizon_bars == mv.horizon_bars, ModelVersion.status == "PROMOTED",
        ModelVersion.id != mv.id).values(status="RETIRED", retired_at=now))
    mv.status, mv.promoted_at, mv.retired_at = "PROMOTED", now, None
    return mv


def retire(mv: ModelVersion) -> ModelVersion:
    if mv.status == "PROMOTED":
        mv.status, mv.retired_at = "RETIRED", utcnow()
    return mv


def serving(db: Session, symbol: str, timeframe: str) -> ModelVersion | None:
    return db.scalar(select(ModelVersion).where(
        ModelVersion.symbol == symbol, ModelVersion.timeframe == timeframe,
        ModelVersion.status == "PROMOTED").order_by(ModelVersion.promoted_at.desc()).limit(1))


def predict(mv: ModelVersion, ctx: C.Ctx, data_source: str, *, quality: str | None = None,
            regime: str | None = None) -> dict[str, Any]:
    """Probabilities for the last closed candle, or an explanation of why not."""
    base = {"model_version": str(mv.id), "model_name": mv.name, "horizon_bars": mv.horizon_bars,
            "algorithm": mv.algorithm, "feature_version": mv.feature_version,
            "model_type": E.model_type_of(mv.params), "decision_ts": ctx.ts[-1],
            "input_quality": quality, "regime": regime,
            "calibration_status": (mv.params.get("calibration") or {}).get(
                "status", "UNCALIBRATED"),
            "order_policy": "A probability or confidence threshold never triggers an order "
                            "on its own; the risk manager must approve any trade.",
            "metrics": {k: mv.metrics[k] for k in ("log_loss", "baseline_log_loss", "brier",
                                                    "baseline_brier", "ece", "oos_samples")},
            "label_definition": mv.metrics.get("label_definition")}
    if data_source != mv.data_source:
        return {**base, "probabilities": None,
                "reason": f"Model trained on {mv.data_source} data; current data is {data_source}."}
    x = F.row(ctx, len(ctx) - 1)
    if x is None:
        return {**base, "probabilities": None, "reason": "Not enough history for the features."}
    try:
        # Uncertainty: normalised entropy (0 = certain, 1 = uniform) plus OOD status.
        out = E.predict(mv.params, x, key=_artifact_key(), horizon=mv.horizon_bars,
                        rv20=ctx.s["rv20"][-1], close=ctx.c[-1])
    except E.ArtifactError as exc:
        return {**base, "probabilities": None, "reason": f"Model artifact unusable: {exc}"}
    if out["uncertainty_detail"]["ood"]["status"] == "OUT_OF_DISTRIBUTION":
        out["ood_note"] = "Inputs are outside the training distribution; treat as unreliable."
    return {**base, **out, "reason": None}


def record(db: Session, mv: ModelVersion, ctx: C.Ctx, pred: dict[str, Any],
           regime: str | None, data_source: str) -> None:
    if not pred.get("probabilities"):
        return
    p = pred["probabilities"]
    db.add(Prediction(model_version_id=mv.id, symbol=mv.symbol, timeframe=mv.timeframe,
                      horizon_bars=mv.horizon_bars, bar_ts=ctx.ts[-1],
                      close=Decimal(str(ctx.c[-1])), p_down=p["DOWN"],
                      p_sideways=p.get("SIDEWAYS", 0.0),
                      p_up=p["UP"], regime=regime, data_source=data_source))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # already recorded for this bar
