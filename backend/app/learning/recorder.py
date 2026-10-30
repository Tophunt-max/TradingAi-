"""Decision recording: every analysis or bot decision, NO TRADE and skipped included.

At most one record per (symbol, timeframe, bar, origin, origin_ref). Recording is
best effort: it runs in a savepoint and a failure never breaks the caller.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.intelligence import DecisionRecord

logger = logging.getLogger("app.learning")
DEFAULT_HORIZON = {"1m": 15, "3m": 10, "5m": 6, "15m": 4, "30m": 4, "1h": 4, "1d": 3}
RECENT_PATTERN_BARS = 3


def _direction(probs: dict[str, float] | None, side: str | None) -> str | None:
    if probs:
        top = max(probs, key=probs.get)  # type: ignore[arg-type]
        return top
    return {"CE": "UP", "PE": "DOWN"}.get(side or "")


def _save(db: Session, rec: DecisionRecord) -> DecisionRecord | None:
    try:
        with db.begin_nested():
            db.add(rec)
            db.flush()
        return rec
    except IntegrityError:
        return None  # already recorded for this bar
    except SQLAlchemyError:
        logger.exception("decision_record_failed")
        return None


def record_analysis(db: Session, *, symbol: str, timeframe: str, out: dict[str, Any],
                    candles: list[dict[str, Any]], quality: str, data_source: str,
                    now: datetime | None = None) -> DecisionRecord | None:
    pred = out.get("prediction") or {}
    probs = out.get("probabilities")
    setup = out.get("setup_candidate")
    regime = out.get("regime") or {}
    patterns = [{"name": p.get("name"), "direction": p.get("direction"),
                 "ts": p["ts"].isoformat() if isinstance(p.get("ts"), datetime) else None}
                for p in out.get("patterns", []) if p.get("bars_ago", 99) < RECENT_PATTERN_BARS]
    mv_id = pred.get("model_version")
    horizon = int(pred.get("horizon_bars") or DEFAULT_HORIZON.get(timeframe, 4))
    calib = "UNCALIBRATED" if not probs else "CALIBRATED_OOS"
    if pred and not probs:
        calib = "ABSTAINED"
    risk = out.get("event_risk") or {}
    rec = DecisionRecord(
        origin="ANALYSIS", origin_ref="", decision_ts=now or utcnow(),
        bar_ts=candles[-1]["ts"], symbol=symbol, timeframe=timeframe, horizon_bars=horizon,
        close=Decimal(str(candles[-1]["close"])), atr=out.get("atr"),
        model_version_id=uuid.UUID(mv_id) if mv_id else None,
        model_name=pred.get("model_name"),
        feature_version=_feature_version() if mv_id else None,
        indicator_version=out.get("indicator_version"), data_quality=quality,
        data_source=data_source, regime=regime.get("label"),
        regime_probabilities=regime.get("probabilities"), probabilities=probs,
        uncertainty=pred.get("uncertainty"), calibration_status=calib,
        predicted_direction=_direction(probs, setup.get("side") if setup else None),
        decision="SETUP_CANDIDATE" if setup else out.get("decision", "NO_TRADE"),
        no_trade_reasons=list(out.get("abstain_reasons", []))[:20],
        strategies=[s["id"] for s in out.get("strategies", [])
                    if s.get("direction") in ("CE", "PE") and s.get("regime_compatible")
                    is not False],
        evidence={"patterns": patterns, "trend": out.get("trend")},
        plan=setup, event_risk=risk.get("level"))
    return _save(db, rec)


def _feature_version() -> str:
    from app.ai import features as F

    return F.FEATURE_VERSION


def record_bot(db: Session, *, bot_id: uuid.UUID, symbol: str, timeframe: str,
               candles: list[dict[str, Any]], atr: float | None, quality: str,
               data_source: str, outcome: str, side: str | None, regime: str | None,
               reasons: list[str], account_mode: str | None,
               order_id: uuid.UUID | None = None, position_id: uuid.UUID | None = None,
               now: datetime | None = None) -> DecisionRecord | None:
    decision = {"SIGNAL": "SIGNAL", "SUPPRESSED": "SKIPPED"}.get(outcome, "NO_TRADE")
    rec = DecisionRecord(
        origin="BOT", origin_ref=bot_id.hex, decision_ts=now or utcnow(),
        bar_ts=candles[-1]["ts"], symbol=symbol, timeframe=timeframe,
        horizon_bars=DEFAULT_HORIZON.get(timeframe, 4),
        close=Decimal(str(candles[-1]["close"])), atr=atr, data_quality=quality,
        data_source=data_source, regime=regime, calibration_status="NOT_APPLICABLE",
        predicted_direction=_direction(None, side) if decision != "NO_TRADE" else None,
        decision=decision, no_trade_reasons=reasons[:20], strategies=[],
        evidence={}, plan={"side": side} if side and decision != "NO_TRADE" else None,
        order_id=order_id, position_id=position_id, account_mode=account_mode)
    return _save(db, rec)


def record_decision(db: Session, *, report: dict[str, Any], candles: list[dict[str, Any]],
                    quality: str, data_source: str, atr: float | None,
                    regime: str | None, now: datetime | None = None) -> DecisionRecord | None:
    """A decision-engine report (TRADE_PROPOSAL / WATCH / NO_TRADE). One per bar, account
    mode and requested side: origin DECISION, origin_ref '<mode>:<side>'."""
    view = report.get("directional_view") or {}
    probs = view.get("probabilities")
    mv_id = view.get("model_version")
    side = report.get("side")
    rec = DecisionRecord(
        origin="DECISION", origin_ref=f"{report['account_mode']}:{report['requested_side']}",
        decision_ts=now or utcnow(), bar_ts=candles[-1]["ts"], symbol=report["symbol"],
        timeframe=report["timeframe"], horizon_bars=DEFAULT_HORIZON.get(report["timeframe"], 4),
        close=Decimal(str(candles[-1]["close"])), atr=atr,
        model_version_id=uuid.UUID(mv_id) if mv_id and probs else None,
        model_name=None, feature_version=_feature_version() if mv_id and probs else None,
        indicator_version=None, data_quality=quality, data_source=data_source, regime=regime,
        regime_probabilities=(report.get("regime") or {}).get("confidence"),
        probabilities=probs, uncertainty=None,
        calibration_status="CALIBRATED_OOS" if probs else "NOT_APPLICABLE",
        predicted_direction=_direction(probs, side) if side else None,
        decision=report["decision"],
        no_trade_reasons=(report.get("no_trade_reasons", [])
                          + report.get("watch_reasons", []))[:20],
        strategies=[],
        evidence={"setup": (report.get("setup") or {}).get("classification"),
                  "votes": (report.get("evidence") or {}).get("votes"),
                  "weights": ((report.get("evidence") or {}).get("combination") or {})
                  .get("weights")},
        plan={"side": side, "sizing": report.get("sizing"),
              "proposal": report.get("proposal")} if side else None,
        event_risk=(report.get("event_risk") or {}).get("level"),
        account_mode=report["account_mode"])
    return _save(db, rec)
