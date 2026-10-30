"""Serving guard: the prediction path abstains when the serving model is DEGRADED."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.learning import drift


def serving_block_reason(db: Session, mv: Any) -> str | None:
    rep = drift.latest(db, mv.id)
    if rep is not None and rep.status == "DEGRADED":
        return ("Model is DEGRADED by drift monitoring (" + " ".join(rep.reasons[:2])
                + ") so it abstains until reviewed, retrained or rolled back.")
    return None
