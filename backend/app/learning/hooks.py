"""Extras stored with a model at training time (called from app.ai.registry).

* `bootstrap`: block-bootstrap CI of the out-of-sample improvement over the
  climatology baseline, used by the promotion policy.
* `feature_reference`: decile bins of every training feature, used for PSI drift.
* `oos_predicted_share`: mix of predicted top classes out of sample, used for
  prediction drift.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.learning import drift
from app.learning import stats as ST


def train_time_extras(X: np.ndarray, wf: dict[str, Any], horizon: int,
                      names: list[str], classes: list[str] | None = None) -> dict[str, Any]:
    P, B, y = wf["P"], wf["B"], wf["y"]
    classes = classes or ST.CLASSES
    top = P.argmax(axis=1)
    return {
        "bootstrap": ST.improvement_vs_baseline(P, B, y, block=max(1, horizon)),
        "feature_reference": drift.reference_from_matrix(X, names),
        "oos_predicted_share": {c: round(float((top == i).mean()), 4)
                                for i, c in enumerate(classes)},
    }
