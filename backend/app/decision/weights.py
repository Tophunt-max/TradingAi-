"""Learned evidence-family weights: fitted and validated, but used only when earned.

`fit_evidence_weights` learns one weight per family with logistic regression
(app.ai.model, binary UP / not-UP over `horizon` bars) on purged, expanding-window
walk-forward splits (app.ai.model.walk_forward). The out-of-sample predictions are
compared with the UNWEIGHTED family-vote rule on the same test rows:

- learned direction = argmax of the fold model's probabilities;
- unweighted direction = sign of the summed family votes (a tie scores 0.5);
- per-sample 0/1 loss, paired moving-block bootstrap (block = horizon).

Learned weights are usable only if (a) the bootstrap CI says they beat the unweighted rule
out of sample, (b) their log loss beats the base-rate baseline, and (c) the data is not
DEMO. Results live in this process only (no database table, no migration); a restart
forgets them, and the engine falls back to the unweighted rule.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import numpy as np

from app.ai import model as M
from app.analysis import patterns as PAT
from app.analysis import smc as SMC
from app.analysis import technical_summary as TS
from app.analysis.context import Ctx
from app.core.time import utcnow
from app.decision import evidence as EV
from app.indicators.evidence import family_votes

TRAIN_FAMILIES = (*EV.TECH_FAMILIES, *EV.EVENT_FAMILIES)  # the model family is a model itself
MIN_ROWS = 200
_LOCK = threading.Lock()
_STORE: dict[tuple[str, str], dict[str, Any]] = {}


def vote_matrix(ctx: Ctx, horizon: int, warmup: int = 60) -> tuple[np.ndarray, np.ndarray,
                                                                    np.ndarray]:
    """Family votes at every bar i (causal: only values known at i) and the label
    UP = close[i + horizon] > close[i]. Returns X, y and the per-row unweighted net vote."""
    n = len(ctx)
    pats = PAT.all_patterns(ctx)
    smc = SMC.analyse(ctx)
    rows, labels = [], []
    for i in range(warmup, n - horizon):
        votes, _ = TS.indicator_votes(ctx, i)
        fam = {f["family"]: f["vote"] for f in family_votes(votes)["families"]}
        recent = [p for p in pats if p.get("confirmation", {}).get("status") == "CONFIRMED"
                  and p["confirmation"].get("index") is not None
                  and i - EV.RECENT_PATTERN_BARS < p["confirmation"]["index"] <= i]
        cv = sum(EV._DIR.get(p["confirmation"].get("direction") or "", 0) for p in recent)
        st = [e for e in smc["structure_events"] + smc["liquidity_sweeps"]
              + smc["failed_breakouts"]
              if i - EV.STRUCTURE_BARS <= e["confirmed_index"] <= i
              and e.get("direction") in EV._DIR]
        sv = EV._DIR[max(st, key=lambda e: e["confirmed_index"])["direction"]] if st else 0
        rows.append([fam.get("trend", 0), fam.get("momentum", 0), fam.get("volume", 0),
                     EV._sign(cv), sv])
        labels.append(1 if ctx.c[i + horizon] > ctx.c[i] else 0)
    X = np.array(rows, dtype=float).reshape(-1, len(TRAIN_FAMILIES))
    return X, np.array(labels, dtype=int), X.sum(axis=1)


def fit_evidence_weights(X: np.ndarray, y: np.ndarray, *, horizon: int, data_source: str,
                         folds: int = 5, seed: int = 0) -> dict[str, Any]:
    reasons: list[str] = []
    base = {"weights": None, "intercept": None, "usable": False, "data_source": data_source,
            "families": list(TRAIN_FAMILIES), "horizon_bars": horizon, "fitted_at": utcnow(),
            "method": "Logistic regression on family votes; purged expanding walk-forward; "
                      "compared with the unweighted vote rule on the same OOS rows."}
    if len(y) < MIN_ROWS or len(set(y.tolist())) < 2:
        return {**base, "metrics": None,
                "reasons": [f"Not enough labelled rows ({len(y)}; need ≥ {MIN_ROWS} with both "
                            "outcomes)."]}
    try:
        wf = M.walk_forward(X, y, horizon, folds=folds, n_classes=2)
    except ValueError as exc:
        return {**base, "metrics": None, "reasons": [str(exc)]}
    P, yy, idx = wf["P"], wf["y"], np.array(wf["test_idx"])
    net = X[idx].sum(axis=1)
    learned_loss = (P.argmax(axis=1) != yy).astype(float)
    unw_pred = np.where(net > 0, 1, np.where(net < 0, 0, -1))
    unw_loss = np.where(unw_pred == -1, 0.5, (unw_pred != yy).astype(float))
    boot = M.block_bootstrap_diff(learned_loss, unw_loss, block=max(horizon, 1), seed=seed)
    ll, bll = M.log_loss(P, yy), M.log_loss(wf["B"], yy)
    metrics = {"oos_samples": int(len(yy)), "learned_error_rate": round(float(
        learned_loss.mean()), 4), "unweighted_error_rate": round(float(unw_loss.mean()), 4),
        "learned_log_loss": round(ll, 5), "baseline_log_loss": round(bll, 5),
        "bootstrap": boot, "folds": wf["folds"]}
    beats_rule = bool(boot["better"])
    beats_base = ll < bll
    if not beats_rule:
        reasons.append("Learned weights do not beat the unweighted vote rule out of sample "
                       f"(error-rate difference {boot['mean_diff']:+.4f}, 95% CI "
                       f"[{boot['ci_low']:+.4f}, {boot['ci_high']:+.4f}]).")
    if not beats_base:
        reasons.append("Learned log loss does not beat the base-rate baseline.")
    if data_source == "DEMO":
        reasons.append("Trained on synthetic DEMO data: learned weights are never used.")
    params = M.fit(X, y, seed=seed, n_classes=2)
    scale = np.array(params.scale)
    coef = (np.array(params.coef[1]) - np.array(params.coef[0])) / scale
    weights = {f: round(float(w), 4) for f, w in zip(TRAIN_FAMILIES, coef, strict=True)}
    intercept = float(params.intercept[1] - params.intercept[0]
                      - np.dot(np.array(params.coef[1]) - np.array(params.coef[0]),
                               np.array(params.mean) / scale))
    return {**base, "weights": weights, "intercept": round(intercept, 4), "metrics": metrics,
            "usable": beats_rule and beats_base and data_source != "DEMO", "reasons": reasons}


def store(symbol: str, timeframe: str, result: dict[str, Any]) -> None:
    with _LOCK:
        _STORE[(symbol, timeframe)] = result


def latest(symbol: str, timeframe: str) -> dict[str, Any] | None:
    with _LOCK:
        return _STORE.get((symbol, timeframe))


def clear() -> None:
    with _LOCK:
        _STORE.clear()


def active(symbol: str, timeframe: str, data_source: str) -> tuple[dict[str, Any] | None, str]:
    """Weights the engine may use now, and why (not) — the live data must be non-DEMO too."""
    r = latest(symbol, timeframe)
    if r is None:
        return None, "No learned weights have been fitted in this process."
    if data_source == "DEMO":
        return None, "Current data is DEMO: learned weights are never used on simulated data."
    if not r["usable"]:
        return None, "Learned weights failed validation: " + " ".join(r["reasons"][:2])
    return r, "Learned weights beat the unweighted rule out of sample on non-DEMO data."


def weighted_combine(votes: dict[str, int | None], w: dict[str, Any],
                     side: str | None) -> dict[str, Any]:
    """P(UP) = sigmoid(intercept + Σ w_f · vote_f). Used only when `active` returns weights."""
    z = w["intercept"] + sum(w["weights"][f] * (votes.get(f) or 0) for f in TRAIN_FAMILIES)
    p_up = 1 / (1 + float(np.exp(-z)))
    direction = 1 if side == "CE" else -1 if side == "PE" else (1 if p_up > 0.5 else -1)
    p_side = p_up if direction > 0 else 1 - p_up
    passed = p_side >= 0.5 + EV.MODEL_EDGE / 2
    return {"weights": "LEARNED_VALIDATED", "p_up": round(p_up, 4), "passed": passed,
            "direction": "BULLISH" if direction > 0 else "BEARISH",
            "side": "CE" if direction > 0 else "PE",
            "reasons": [] if passed else [f"Weighted evidence P({'UP' if direction > 0 else
                                                                 'DOWN'}) {p_side:.2f} "
                                          f"is below {0.5 + EV.MODEL_EDGE / 2:.2f}."]}


def public(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if r is None:
        return None
    out = dict(r)
    if isinstance(out.get("fitted_at"), datetime):
        out["fitted_at"] = out["fitted_at"].isoformat()
    return out
