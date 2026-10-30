"""AI Intelligence Dashboard (work package O): read-only aggregation of existing services.

Every section has `status` OK / NO_DATA / UNAVAILABLE, a `source` and labels. Nothing is
estimated here: missing inputs stay null with the reason. Account-scoped sections
(strategy performance, account results, risk) use the user's ACTIVE account only, so
REAL numbers never appear in a DEMO dashboard and the reverse."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.accounts import core as ACC
from app.ai import registry
from app.api.deps import AuthDep, DbDep, SettingsDep
from app.api.v1.analysis import _REGIME_EVENT_LEVEL, _candles
from app.api.v1.decision import prediction_for
from app.api.v1.system import live_blockers
from app.core.time import ensure_utc, utcnow
from app.decision import engine as ENG
from app.events import store as events_store
from app.learning import drift, metrics
from app.market_data.factory import ProviderDep, provider_errors
from app.models.bots import Bot
from app.models.execution import Position
from app.models.intelligence import DecisionRecord
from app.models.ml import ModelVersion
from app.services import risk as risk_svc
from app.services.accounts import real_unavailable_reasons

router = APIRouter(prefix="/intelligence", tags=["intelligence"])
logger = logging.getLogger("app.intelligence")

HISTORY = 10
SUMMARY_LIMIT = 2000
D = Decimal


def _section(status: str, source: str, data: Any = None, reason: str | None = None,
             **labels: Any) -> dict[str, Any]:
    return {"status": status, "source": source, "data": data, "reason": reason, **labels}


def _safe(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # one failing input never hides the others
        logger.warning("dashboard_section_failed", extra={"section": name})
        msg = getattr(exc, "message", None) or type(exc).__name__
        return _section("UNAVAILABLE", name, reason=str(msg))


def _record_out(r: DecisionRecord) -> dict[str, Any]:
    return {"id": str(r.id), "origin": r.origin, "decision": r.decision,
            "decision_ts": r.decision_ts, "bar_ts": r.bar_ts, "timeframe": r.timeframe,
            "probabilities": r.probabilities, "predicted_direction": r.predicted_direction,
            "outcome_status": r.outcome_status, "realized_direction": r.realized_direction,
            "data_source": r.data_source, "account_mode": r.account_mode,
            "reasons": (r.no_trade_reasons or [])[:3]}


@router.get("/dashboard")
def dashboard(ctx: AuthDep, db: DbDep, settings: SettingsDep, provider: ProviderDep,
              underlying: Annotated[Literal["NIFTY", "BANKNIFTY"], Query()] = "NIFTY",
              timeframe: Annotated[Literal["5m", "15m", "1h"], Query()] = "15m"
              ) -> dict[str, Any]:
    sym, uid = underlying, ctx.user.id
    mode, acc = ACC.resolve(db, uid, None)
    now = utcnow()
    label = "DEMO_VIRTUAL" if mode == "DEMO" else "REAL"
    state: dict[str, Any] = {}

    def candles() -> Any:
        if "r" not in state:
            state["r"] = _candles(provider, sym, timeframe)
        return state["r"]

    def regime() -> dict[str, Any]:
        r = candles()
        ev = events_store.gate_for(db, settings, sym, now, sym)
        view = ENG.market_view(r.data, timeframe, r.quality.status,
                               _REGIME_EVENT_LEVEL[ev["level"]])
        state["view"] = view
        rv = view["regime_v2"]
        return _section("OK", "regime-v2", {
            "state": rv["state"], "confidence": rv["confidence"], "top": rv["top"],
            "calibration_status": rv["calibration_status"], "no_trade": rv["no_trade"],
            "reasons": rv["reasons"][:4], "version": rv["version"]},
            data_label="SIMULATED" if r.is_demo else r.source)

    def models() -> dict[str, Any]:
        rows = db.execute(select(ModelVersion.status, func.count()).where(
            ModelVersion.symbol == sym).group_by(ModelVersion.status)).all()
        counts = {s: n for s, n in rows}
        latest = db.scalar(select(ModelVersion).where(ModelVersion.symbol == sym)
                           .order_by(ModelVersion.created_at.desc()).limit(1))
        promoted = registry.serving(db, sym, timeframe)
        data = {"promoted": counts.get("PROMOTED", 0), "shadow": counts.get("SHADOW", 0),
                "candidates": counts.get("CANDIDATE", 0), "rejected": counts.get("REJECTED", 0),
                "retired": counts.get("RETIRED", 0),
                "serving": None if promoted is None else {
                    "id": str(promoted.id), "name": promoted.name,
                    "promoted_at": promoted.promoted_at},
                "latest": None if latest is None else {
                    "id": str(latest.id), "name": latest.name, "status": latest.status,
                    "timeframe": latest.timeframe, "created_at": latest.created_at,
                    "data_source": latest.data_source}}
        return _section("OK" if counts else "NO_DATA", "model_registry", data,
                        None if counts else f"No model has been trained for {sym}.")

    def forecast() -> dict[str, Any]:
        r = candles()
        view = state.get("view") or ENG.market_view(r.data, timeframe, r.quality.status, None)
        pred = prediction_for(db, sym, timeframe, view["ctx"], r)
        if pred is None:
            return _section("NO_DATA", "promoted_model", {"probabilities": None},
                            f"No promoted model for {sym} {timeframe}; no probability is "
                            "estimated.")
        if not pred.get("probabilities"):
            return _section("NO_DATA", "promoted_model", {"probabilities": None,
                                                          "model_version": pred.get(
                                                              "model_version")},
                            pred.get("reason"))
        return _section("OK", "promoted_model", {
            "probabilities": pred["probabilities"], "model_version": pred.get("model_version"),
            "model_name": pred.get("model_name"), "uncertainty": pred.get("uncertainty"),
            "calibration_status": pred.get("calibration_status"),
            "horizon_bars": pred.get("horizon_bars")})

    summary_cache: dict[str, Any] = {}

    def learning_summary() -> dict[str, Any]:
        if "s" not in summary_cache:
            rows = db.scalars(select(DecisionRecord).where(DecisionRecord.symbol == sym)
                              .order_by(DecisionRecord.decision_ts.desc())
                              .limit(SUMMARY_LIMIT)).all()
            summary_cache["s"] = metrics.summary(list(rows))
        return summary_cache["s"]

    def calibration() -> dict[str, Any]:
        p = learning_summary()["probabilistic"]
        if not p:
            return _section("NO_DATA", "learning_summary", None,
                            "No resolved probabilistic decisions yet; calibration (ECE, "
                            "reliability) cannot be measured.")
        return _section("OK", "learning_summary", {
            "samples": p["samples"], "ece": p["ece"], "brier": p["brier"],
            "climatology_brier": p["climatology_brier"], "reliability": p["calibration"]})

    def history() -> dict[str, Any]:
        rows = db.scalars(select(DecisionRecord).where(DecisionRecord.symbol == sym)
                          .order_by(DecisionRecord.decision_ts.desc()).limit(HISTORY)).all()
        if not rows:
            return _section("NO_DATA", "decision_records", [], "No decisions recorded yet.")
        return _section("OK", "decision_records", [_record_out(r) for r in rows])

    def strategies() -> dict[str, Any]:
        if acc is None:
            return _section("NO_DATA", "positions", [], "No account connected for this mode.",
                            account_mode=mode, label=label)
        rows = db.execute(select(Position, Bot.strategy_id).outerjoin(
            Bot, Bot.id == Position.bot_id).where(
            Position.account_id == acc.id, Position.status == "CLOSED")).all()
        by: dict[str, dict[str, Any]] = {}
        for p, sid in rows:
            e = by.setdefault(sid or "manual", {"strategy": sid or "manual", "trades": 0,
                                                "wins": 0, "net_pnl": D("0")})
            e["trades"] += 1
            e["wins"] += 1 if p.realized_net > 0 else 0
            e["net_pnl"] += p.realized_net
        out = [{**e, "net_pnl": str(e["net_pnl"]),
                "win_rate": round(e["wins"] / e["trades"], 4) if e["trades"] else None}
               for e in sorted(by.values(), key=lambda x: x["strategy"])]
        return _section("OK" if out else "NO_DATA", "positions", out,
                        None if out else f"No closed {mode} trades yet.", account_mode=mode,
                        label=label, note="Net of estimated charges. Small samples say "
                                          "little about future results.")

    def backtests() -> dict[str, Any]:
        return _section("UNAVAILABLE", "backtesting", None,
                        "Backtests are run on demand and not stored; open the Backtesting Lab "
                        "to run one.", link="/backtesting", endpoint="POST /api/v1/backtests")

    def results() -> dict[str, Any]:
        if acc is None:
            return _section("NO_DATA", "positions", None, "No account connected for this mode.",
                            account_mode=mode, label=label)
        net, n = db.execute(select(
            func.coalesce(func.sum(Position.realized_gross - Position.charges_total), 0),
            func.count()).where(Position.account_id == acc.id,
                                Position.status == "CLOSED")).one()
        return _section("OK" if n else "NO_DATA", "positions",
                        {"realized_net": str(D(net)), "closed_trades": int(n)},
                        None if n else f"No closed {mode} trades yet.", account_mode=mode,
                        label=label)

    def risk() -> dict[str, Any]:
        rs = risk_svc.get_or_create(db, uid)
        reasons = real_unavailable_reasons(db, settings, acc)[:2] if mode == "REAL" else []
        st = risk_svc.status(rs, db, settings, account=acc, account_mode=mode,
                             account_reasons=reasons)
        u, lim = st.usage, st.limits
        return _section("OK", "risk_manager", {
            "new_entries_allowed": st.new_entries_allowed, "block_reasons": st.block_reasons,
            "daily_loss_used_pct": u.daily_loss_used_pct,
            "entries": f"{u.entries_used} / {lim.max_entries_per_day}",
            "open_positions": f"{u.open_positions} / {lim.max_open_positions}",
            "kill_switch_engaged": lim.kill_switch_engaged}, account_mode=mode, label=label)

    def freshness() -> dict[str, Any]:
        r = candles()
        last_ts = ensure_utc(r.data[-1]["ts"])
        quote_age = None
        try:
            with provider_errors():
                q = provider.quotes([sym], "INDEX")
            quote_age = round((now - ensure_utc(q.as_of)).total_seconds(), 1)
        except Exception:
            logger.warning("dashboard_quote_unavailable", extra={"symbol": sym})
        try:
            pst = provider.status()
        except Exception:
            pst = {"status": "unknown"}
        return _section("OK", "market_data", {
            "provider": r.provider, "data_source": r.source, "is_demo": r.is_demo,
            "last_candle_ts": last_ts,
            "last_candle_age_seconds": round((now - last_ts).total_seconds(), 1),
            "last_quote_age_seconds": quote_age, "provider_status": pst.get("status"),
            "quality": r.quality.as_dict()},
            data_label="SIMULATED" if r.is_demo else r.source)

    def broker() -> dict[str, Any]:
        real = ACC.get_account(db, uid, "REAL")
        reasons = real_unavailable_reasons(db, settings, real)
        return _section("UNAVAILABLE", "system_status", {
            "paper_adapter": "OK" if settings.paper_trading_enabled else "DISABLED",
            "real_broker_connected": real is not None and real.status == "ACTIVE",
            "live_trading_enabled": False, "reasons": reasons,
            "blockers": live_blockers(settings.market_data_mode)},
            "No verified real-broker connection; live trading is locked.")

    def model_drift() -> dict[str, Any]:
        mvs = db.scalars(select(ModelVersion).where(
            ModelVersion.symbol == sym,
            ModelVersion.status.in_(("PROMOTED", "SHADOW")))).all()
        if not mvs:
            return _section("NO_DATA", "learning_drift", [],
                            "No promoted or shadow model to monitor.")
        out = []
        for m in mvs:
            rep = drift.latest(db, m.id)
            out.append({"model_id": str(m.id), "model_name": m.name, "model_status": m.status,
                        "status": rep.status if rep else "NOT_COMPUTED",
                        "computed_at": rep.computed_at if rep else None,
                        "reasons": (rep.reasons if rep else
                                    ["Drift has not been computed yet."])[:3]})
        return _section("OK", "learning_drift", out)

    def errors() -> dict[str, Any]:
        s = learning_summary()
        if not s["resolved"]:
            return _section("NO_DATA", "learning_summary", None,
                            "No resolved decisions yet; errors cannot be attributed.")
        return _section("OK", "learning_summary", {"resolved": s["resolved"],
                                                   "error_classes": s["error_classes"]})

    def no_trade() -> dict[str, Any]:
        rec = db.scalar(select(DecisionRecord).where(
            DecisionRecord.symbol == sym, DecisionRecord.origin == "DECISION",
            DecisionRecord.account_mode == mode)
            .order_by(DecisionRecord.decision_ts.desc()).limit(1))
        if rec is None:
            return _section("NO_DATA", "decision_records", None,
                            "No decision report yet; open the Decision Report to build one.",
                            link=f"/decision?underlying={sym}", account_mode=mode)
        return _section("OK", "decision_records", {
            "decision": rec.decision, "decision_ts": rec.decision_ts,
            "timeframe": rec.timeframe, "reasons": rec.no_trade_reasons},
            link=f"/decision?underlying={sym}", account_mode=mode)

    sections = {name: _safe(name, fn) for name, fn in (
        ("regime", regime), ("models", models), ("forecast", forecast),
        ("calibration", calibration), ("prediction_history", history),
        ("strategy_performance", strategies), ("backtests", backtests),
        ("account_results", results), ("risk", risk), ("data_freshness", freshness),
        ("broker", broker), ("drift", model_drift), ("error_attribution", errors),
        ("no_trade", no_trade))}
    db.commit()
    r = state.get("r")
    return {"underlying": sym, "timeframe": timeframe, "account_mode": mode,
            "account_label": label, "as_of": now,
            "data_source": r.source if r is not None else None,
            "is_demo_data": r.is_demo if r is not None else None,
            "sections": sections,
            "note": "Read-only. Every value comes from a stored record or a live service; "
                    "missing inputs are shown as NO_DATA or UNAVAILABLE, never estimated."}

