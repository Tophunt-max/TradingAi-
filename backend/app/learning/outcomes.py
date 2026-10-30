"""Outcome scorer: resolves decision records and model predictions after their horizon.

For a decision on the candle starting at `bar_ts` with horizon H, the outcome uses
only the H candles that follow the decision candle. A record is touched only when
(a) `now >= bar_ts + (H + 1) × timeframe` and (b) those H closed candles exist.
Labels match training: UP/DOWN if the close H bars ahead moved more than
0.5 × ATR(14) at decision time, else SIDEWAYS.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import ensure_utc
from app.learning.attribution import attribute
from app.models.execution import Fill, Order, Position
from app.models.intelligence import DecisionRecord
from app.models.ml import Prediction

logger = logging.getLogger("app.learning")
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
LABEL_ATR_MULT = 0.5
FETCH = 1000
WINDOW = 299  # candles of context (plus the end candle) for regime/pattern/ATR


def horizon_elapsed(bar_ts: datetime, timeframe: str, horizon: int, now: datetime) -> bool:
    return ensure_utc(now) >= ensure_utc(bar_ts) + timedelta(
        minutes=TF_MINUTES[timeframe] * (horizon + 1))


def label(move: float, atr: float | None) -> str | None:
    if atr is None or atr <= 0:
        return None
    if move > LABEL_ATR_MULT * atr:
        return "UP"
    if move < -LABEL_ATR_MULT * atr:
        return "DOWN"
    return "SIDEWAYS"


def compute_outcome(candles: list[dict[str, Any]], t: int, horizon: int, atr: float | None,
                    predicted: str | None) -> dict[str, Any]:
    """Pure: realized outcome of a decision at index t from candles t+1..t+H."""
    c0 = float(candles[t]["close"])
    fut = candles[t + 1:t + 1 + horizon]
    c1 = float(fut[-1]["close"])
    highs = [float(c["high"]) for c in fut]
    lows = [float(c["low"]) for c in fut]
    closes = [c0] + [float(c["close"]) for c in fut]
    rets = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:], strict=True) if a > 0]
    vol = (math.sqrt(sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1))
           if len(rets) > 1 else None)
    if predicted == "DOWN":
        mfe, mae = c0 - min(lows), max(highs) - c0
    else:
        mfe, mae = max(highs) - c0, c0 - min(lows)
    late = None
    after = candles[t + 1 + horizon:t + 1 + 2 * horizon]
    if len(after) == horizon:
        late = label(float(after[-1]["close"]) - c0, atr)
    return {"realized_return": round(c1 / c0 - 1, 6), "realized_direction": label(c1 - c0, atr),
            "realized_volatility": None if vol is None else round(vol, 6),
            "max_favorable_excursion": round(mfe, 4), "max_adverse_excursion": round(mae, 4),
            "threshold": None if atr is None else LABEL_ATR_MULT * atr, "late_direction": late}


def _links(db: Session, rec: DecisionRecord) -> dict[str, Any]:
    out: dict[str, Any] = {}
    order = db.get(Order, rec.order_id) if rec.order_id else None
    if order is not None:
        out["order_status"] = order.status
        ref = order.risk_snapshot.get("entry_price") if order.risk_snapshot else None
        fills = db.scalars(select(Fill).where(Fill.order_id == order.id)).all()
        if fills and ref:
            qty = sum(f.quantity for f in fills)
            avg = sum(f.price * f.quantity for f in fills) / qty
            ref_d = Decimal(str(ref))
            slip = (avg - ref_d) if order.side == "BUY" else (ref_d - avg)
            out["slippage"] = slip.quantize(Decimal("0.0001"))
            out["slippage_pct"] = float(slip / ref_d * 100) if ref_d else None
        pos_id = rec.position_id or order.position_id
    else:
        pos_id = rec.position_id
    pos = db.get(Position, pos_id) if pos_id else None
    if pos is not None:
        mark = pos.avg_exit if pos.avg_exit is not None else pos.last_price
        if mark is not None:
            out["premium_change"] = (mark - pos.avg_entry).quantize(Decimal("0.0001"))
    return out


def _ctx_regime(candles: list[dict[str, Any]], end: int, timeframe: str) -> str | None:
    from app.analysis import context as C
    from app.analysis import regime as R

    try:
        ctx = C.build(candles[max(0, end - WINDOW):end + 1], intraday=timeframe != "1d")
        reg = R.detect(ctx)
        return reg.get("label") if reg else None
    except Exception:  # noqa: BLE001 - regime is optional context
        return None


def _invalidated(candles: list[dict[str, Any]], end: int, timeframe: str,
                 cited: list[dict[str, Any]]) -> list[str]:
    if not cited:
        return []
    from app.analysis import context as C
    from app.analysis import patterns as PAT

    ctx = C.build(candles[max(0, end - WINDOW):end + 1], intraday=timeframe != "1d")
    want = {(p["name"], p["ts"]) for p in cited if p.get("ts")}
    return sorted({e["name"] for e in PAT.all_patterns(ctx)
                   if (e["name"], e["ts"].isoformat()) in want
                   and e.get("confirmation", {}).get("status") == "INVALIDATED"})


def _atr_at(candles: list[dict[str, Any]], t: int, timeframe: str) -> float | None:
    from app.analysis import context as C

    ctx = C.build(candles[max(0, t - WINDOW):t + 1], intraday=timeframe != "1d")
    return ctx.s["atr14"][-1]


def resolve_outcomes(db: Session, provider: Any, now: datetime, *,
                     slippage_threshold_pct: float = 1.0, limit: int = 500) -> dict[str, int]:
    now = ensure_utc(now)
    stats = {"resolved": 0, "unresolvable": 0, "waiting": 0, "predictions_resolved": 0}
    pending = db.scalars(select(DecisionRecord).where(DecisionRecord.outcome_status == "PENDING")
                         .order_by(DecisionRecord.bar_ts).limit(limit)).all()
    preds = db.scalars(select(Prediction).where(Prediction.outcome.is_(None))
                       .order_by(Prediction.bar_ts).limit(limit)).all()
    targets: dict[Any, str] = {}
    groups: dict[tuple[str, str], list[Any]] = {}
    for x in [*pending, *preds]:
        if not horizon_elapsed(x.bar_ts, x.timeframe, x.horizon_bars, now):
            stats["waiting"] += 1
            continue
        groups.setdefault((x.symbol, x.timeframe), []).append(x)
    for (symbol, tf), items in groups.items():
        try:
            candles = provider.candles(symbol, tf, FETCH).data
        except Exception:  # noqa: BLE001 - try again next pass
            logger.warning("outcome_candles_unavailable", extra={"symbol": symbol, "tf": tf})
            continue
        index = {ensure_utc(c["ts"]): i for i, c in enumerate(candles)}
        first = ensure_utc(candles[0]["ts"]) if candles else None
        for x in items:
            t = index.get(ensure_utc(x.bar_ts))
            if t is None:
                if isinstance(x, DecisionRecord) and first is not None and x.bar_ts < first:
                    x.outcome_status, x.resolved_at = "UNRESOLVABLE", now
                    x.outcome_notes = ["Decision candle is older than the available history."]
                    stats["unresolvable"] += 1
                continue
            if t + x.horizon_bars >= len(candles):
                stats["waiting"] += 1
                continue  # horizon candles not closed yet
            if isinstance(x, Prediction):
                if _resolve_prediction(db, x, candles, t, tf, now, targets):
                    stats["predictions_resolved"] += 1
                continue
            _resolve_record(db, x, candles, t, tf, now, slippage_threshold_pct)
            stats["resolved"] += 1
    db.flush()
    return stats


def _resolve_prediction(db: Session, p: Prediction, candles: list[dict[str, Any]], t: int,
                        tf: str, now: datetime, targets: dict[Any, str]) -> bool:
    """Label a served/shadow prediction with the model's own target definition."""
    from app.learning.stats import target_of
    from app.models.ml import ModelVersion

    if p.model_version_id not in targets:
        mv = db.get(ModelVersion, p.model_version_id)
        targets[p.model_version_id] = target_of(mv) if mv else "three_class"
    move = float(candles[t + p.horizon_bars]["close"]) - float(candles[t]["close"])
    if targets[p.model_version_id] == "binary":
        outcome = None if move == 0 else ("UP" if move > 0 else "DOWN")
        p.outcome, p.resolved_at = outcome or "TIE", now
        return outcome is not None
    outcome = label(move, _atr_at(candles, t, tf))
    if outcome is None:
        return False
    p.outcome, p.resolved_at = outcome, now
    return True


def _resolve_record(db: Session, rec: DecisionRecord, candles: list[dict[str, Any]], t: int,
                    tf: str, now: datetime, slip_pct: float) -> None:
    atr = rec.atr if rec.atr is not None else _atr_at(candles, t, tf)
    o = compute_outcome(candles, t, rec.horizon_bars, atr, rec.predicted_direction)
    end = t + rec.horizon_bars
    o["regime_at_resolution"] = _ctx_regime(candles, end, tf)
    o["invalidated_patterns"] = _invalidated(candles, end, tf,
                                             (rec.evidence or {}).get("patterns", []))
    links = _links(db, rec)
    d = {"data_quality": rec.data_quality, "predicted_direction": rec.predicted_direction,
         "regime": rec.regime, "predicted_vol_range": rec.predicted_vol_range}
    rec.error_classes = attribute(d, o, links, slip_pct)
    rec.realized_return = o["realized_return"]
    rec.realized_direction = o["realized_direction"]
    rec.realized_volatility = o["realized_volatility"]
    rec.max_favorable_excursion = o["max_favorable_excursion"]
    rec.max_adverse_excursion = o["max_adverse_excursion"]
    rec.regime_at_resolution = o["regime_at_resolution"]
    rec.option_premium_change = links.get("premium_change")
    rec.slippage = links.get("slippage")
    notes = []
    if o["invalidated_patterns"]:
        notes.append("Invalidated: " + ", ".join(o["invalidated_patterns"]) + ".")
    if rec.predicted_direction is None:
        notes.append("No direction was predicted; the outcome is recorded for NO TRADE "
                     "analysis only.")
    rec.outcome_notes = notes
    rec.outcome_status, rec.resolved_at = "RESOLVED", now
