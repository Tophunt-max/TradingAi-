"""Decision engine API (work package L). Read-only: it never places or reserves an order.

GET /decision/{underlying} builds a DecisionReport for the user's ACTIVE account. A
TRADE_PROPOSAL contains a `paper_order_request` that the app may submit to
POST /orders/paper after the user confirms; the risk manager re-checks it there."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import ConfigDict
from sqlalchemy import select

from app.accounts import core as ACC
from app.ai import registry
from app.analysis import pipeline
from app.analysis import regime as R
from app.api.deps import AdminDep, AuthDep, DbDep, SettingsDep, user_rate_limit
from app.api.v1.analysis import _REGIME_EVENT_LEVEL, _candles
from app.api.v1.market import Provenance
from app.core.time import utcnow
from app.decision import engine as ENG
from app.decision import evidence as EV
from app.decision import sizing as SZ
from app.decision import weights as W
from app.events import store as events_store
from app.execution import charges as CH
from app.execution import risk as RISK
from app.execution import service as EX
from app.fundamentals import service as fundamentals_service
from app.fundamentals import store as fundamentals_store
from app.learning import guard as learning_guard
from app.learning import recorder as learning_recorder
from app.market_data.factory import ProviderDep, provider_errors
from app.models.intelligence import DecisionRecord
from app.options import intelligence as INTEL
from app.services import risk as risk_svc
from app.services.accounts import real_unavailable_reasons
from app.services.audit import record_audit

router = APIRouter(prefix="/decision", tags=["decision"])
logger = logging.getLogger("app.decision")

Underlying = Literal["NIFTY", "BANKNIFTY"]
TF = Literal["5m", "15m", "1h"]
STOP_PCT = Decimal("20")
TARGET_PCT = Decimal("30")
HORIZON_MINUTES = 60
PATHS = 2000
FIT_WINDOW = 600


class DecisionReport(Provenance):
    model_config = ConfigDict(extra="allow")

    version: str
    symbol: str
    timeframe: str
    account_mode: Literal["DEMO", "REAL"]
    label: str
    decision: Literal["NO_TRADE", "WATCH", "TRADE_PROPOSAL"]
    requested_side: str
    side: str | None
    no_trade_reasons: list[str]
    watch_reasons: list[str]
    proposal: dict[str, Any] | None
    execution: dict[str, Any]
    decision_record_id: str | None = None
    recorded: str | None = None


def prediction_for(db: Any, sym: str, timeframe: str, ctx: Any, r: Any) -> dict[str, Any] | None:
    """PROMOTED model only (never shadow or candidate); abstains on FAIL data or drift."""
    mv = registry.serving(db, sym, timeframe)
    if mv is None:
        return None
    if r.quality.status == "FAIL":
        return {"model_version": str(mv.id), "model_name": mv.name, "probabilities": None,
                "reason": "Data-quality check failed, so the model abstains."}
    blocked = learning_guard.serving_block_reason(db, mv)
    if blocked:
        return {"model_version": str(mv.id), "model_name": mv.name, "probabilities": None,
                "reason": blocked}
    legacy = R.detect(ctx)
    return registry.predict(mv, ctx, r.source, quality=r.quality.status,
                            regime=legacy["label"] if legacy else None)


def _fundamentals(db: Any, provider: Any, settings: Any, sym: str, now: Any) -> dict[str, Any]:
    try:
        out = fundamentals_service.build(db, provider, sym, now,
                                         settings.fundamentals_stale_days)
        return {"status": out.get("status"), "message": out.get("message"), "as_of": now}
    except Exception:
        logger.warning("decision_fundamentals_unavailable", extra={"symbol": sym})
        return {"status": "UNAVAILABLE", "message": "Fundamentals service failed.",
                "as_of": now}


def _demo_blockers(acc: Any) -> list[str]:
    return [] if acc.status == "ACTIVE" else [f"The demo account is {acc.status}."]


@router.get("/{underlying}", response_model=DecisionReport,
            dependencies=[Depends(user_rate_limit("decision"))])
def decision_report(
    ctx: AuthDep, db: DbDep, settings: SettingsDep, provider: ProviderDep,
    underlying: Annotated[Underlying, Path()],
    timeframe: Annotated[TF, Query()] = "15m",
    side: Annotated[Literal["auto", "CE", "PE"], Query()] = "auto",
) -> DecisionReport:
    sym = underlying
    uid = ctx.user.id
    mode, acc = ACC.resolve(db, uid, None)
    r = _candles(provider, sym, timeframe)
    now = utcnow()
    prof = fundamentals_store.profile(db, sym, now)
    event = events_store.gate_for(db, settings, sym, now, sym, prof.sector if prof else None)
    view = ENG.market_view(r.data, timeframe, r.quality.status,
                           _REGIME_EVENT_LEVEL[event["level"]])
    prediction = prediction_for(db, sym, timeframe, view["ctx"], r)
    collected = EV.collect(technical=view["technical"], patterns=view["patterns"],
                           smc=view["smc"], prediction=prediction, n=view["n"],
                           ts=view["last_ts"])
    req = None if side == "auto" else side
    combined = EV.combine(collected["votes"], collected["groups"], req)
    learned, w_reason = W.active(sym, timeframe, r.source)
    if learned is not None:
        wc = W.weighted_combine(collected["votes"], learned, req)
        combined = {**combined, **wc, "unweighted": dict(combined)}
    weights_info = {"used": combined["weights"], "learned_status": w_reason}
    fundamentals = _fundamentals(db, provider, settings, sym, now)
    rs = risk_svc.get_or_create(db, uid)

    s = combined.get("side")
    options = candidate = sizing = risk = setup_levels = None
    chain_r = None
    if s is not None:
        setup_levels = pipeline._setup(view["ctx"], s)
        try:
            with provider_errors():
                chain_r = provider.option_chain(sym, None, 5)
        except Exception:
            logger.warning("decision_chain_unavailable", extra={"symbol": sym})
    if chain_r is not None:
        available = RISK.funds(db, acc)["available"] if acc is not None else None
        options = INTEL.analyse(
            chain=chain_r.data, source=chain_r.source, quality_status=chain_r.quality.status,
            side=s, now=chain_r.as_of, strikes=2, stop_pct=float(STOP_PCT),
            target_pct=float(TARGET_PCT), horizon_minutes=HORIZON_MINUTES,
            ctx=view["ctx"] if r.quality.status != "FAIL" else None,
            direction=collected["model"], available_funds=float(available)
            if available is not None else None, risk_per_trade=float(rs.max_risk_per_trade),
            brokerage=settings.paper_brokerage_per_order, demo_lot_size=settings.demo_lot_size,
            paths=PATHS)
        blocked_chain = EX.chain_blocked(chain_r)
        # Eligible candidates are ranked first; an ineligible top candidate is passed on so
        # the report shows why it is blocked.
        top = options["candidates"][0] if options["candidates"] else None
        candidate = top if not blocked_chain else None
        if candidate is not None and candidate["eligible"] and candidate.get("ask") \
                and options.get("lot_size") and options.get("tick_size"):
            entry = Decimal(str(candidate["ask"]))
            day = RISK.trading_day(now)
            sizing = SZ.propose(entry_premium=entry, stop_pct=STOP_PCT, target_pct=TARGET_PCT,
                                lot_size=int(options["lot_size"]),
                                tick=Decimal(str(options["tick_size"])),
                                max_risk_per_trade=rs.max_risk_per_trade,
                                max_lots=rs.max_lots_per_trade,
                                brokerage=settings.paper_brokerage_per_order, day=day,
                                reduce_exposure=event["action"] == "REDUCE_EXPOSURE")
            if mode == "DEMO" and acc is not None:
                # With 0 proposed lots, check 1 lot so the user still sees every limit.
                lots = max(sizing["lots"], 1)
                stop = Decimal(sizing["stop_premium"])
                rt = CH.round_trip(buy=entry, sell=stop, qty=lots * int(options["lot_size"]),
                                   day=day, brokerage=settings.paper_brokerage_per_order)
                dec = RISK.pretrade_dry_run(
                    db, settings, rs, account=acc, now=now, lots=lots,
                    lot_size=int(options["lot_size"]), entry_price=entry, stop_price=stop,
                    round_trip_charges=Decimal(rt["total"]["total"]),
                    buy_charges=Decimal(rt["buy"]["total"]),
                    quote_ok=bool(candidate.get("bid") and candidate.get("ask")),
                    quote_detail="No fresh two-sided quote for this contract.",
                    expiry=options["expiry"], account_blockers=_demo_blockers(acc))
                risk = {"allowed": dec.allowed, "reasons": dec.reasons, "checks": dec.checks,
                        "snapshot": dec.snapshot, "dry_run": True, "lots_checked": lots,
                        "note": "Read-only dry run of the risk manager: nothing was placed or "
                                "reserved. The same checks run again at order time."}
    if mode == "DEMO":
        execution = {"available": True, "reason": None, "route": "POST /api/v1/orders/paper",
                     "note": "DEMO account: a confirmed proposal becomes a simulated paper "
                             "order."}
    else:
        reasons = real_unavailable_reasons(db, settings, acc)
        execution = {"available": False, "route": None, "reasons": reasons,
                     "reason": "REAL account: execution unavailable while live trading is "
                               "locked (" + " ".join(reasons[:2]) + ")"}
        if s is not None:
            risk = {"allowed": False, "reasons": reasons[:2], "checks": [], "dry_run": True,
                    "note": "The REAL account cannot trade in this build."}

    report = ENG.decide(
        symbol=sym, timeframe=timeframe, account_mode=mode, is_demo=r.is_demo,
        data_source=r.source, quality=r.quality.as_dict(), view=view, collected=collected,
        combined=combined, event_risk=event, fundamentals=fundamentals, options=options,
        candidate=candidate if s is not None else None, sizing=sizing, risk=risk,
        setup_levels=setup_levels, execution=execution, weights_info=weights_info,
        requested_side=side, now=now)
    if chain_r is not None and EX.chain_blocked(chain_r):
        report["no_trade_reasons"].insert(0, "Option chain failed quality checks.")
    legacy = R.detect(view["ctx"])
    rec = learning_recorder.record_decision(
        db, report=report, candles=r.data, quality=r.quality.status, data_source=r.source,
        atr=view["atr"], regime=legacy["label"] if legacy else None, now=now)
    recorded = "NEW"
    if rec is None:
        rec = db.scalar(select(DecisionRecord).where(
            DecisionRecord.symbol == sym, DecisionRecord.timeframe == timeframe,
            DecisionRecord.bar_ts == r.data[-1]["ts"], DecisionRecord.origin == "DECISION",
            DecisionRecord.origin_ref == f"{mode}:{side}"))
        recorded = "ALREADY_RECORDED_FOR_BAR" if rec is not None else "NOT_RECORDED"
    db.commit()
    report.pop("as_of", None)
    return DecisionReport(**r.provenance(), **report,
                          decision_record_id=str(rec.id) if rec is not None else None,
                          recorded=recorded)


# ---------------------------------------------------------------- weights


@router.get("/weights/status")
def weights_status(_: AuthDep, underlying: Annotated[Underlying, Query()] = "NIFTY",
                   timeframe: Annotated[TF, Query()] = "15m") -> dict[str, Any]:
    r = W.latest(underlying, timeframe)
    return {"default": EV.WEIGHTS_LABEL, "rule": EV.RULE, "latest_fit": W.public(r),
            "storage": "In-process only (no database table); a restart clears it.",
            "used_when": "Validation beats the unweighted rule out of sample AND the data "
                         "(both training and live) is non-DEMO."}


@router.post("/admin/weights/fit")
def fit_weights(admin: AdminDep, provider: ProviderDep, request: Request, db: DbDep,
                underlying: Annotated[Underlying, Query()] = "NIFTY",
                timeframe: Annotated[TF, Query()] = "15m") -> dict[str, Any]:
    from app.analysis import context as C

    with provider_errors():
        r = provider.candles(underlying, timeframe, FIT_WINDOW)
    horizon = learning_recorder.DEFAULT_HORIZON.get(timeframe, 4)
    ctx = C.build(r.data, intraday=True, timeframe=timeframe)
    X, y, _ = W.vote_matrix(ctx, horizon)
    out = W.fit_evidence_weights(X, y, horizon=horizon, data_source=r.source)
    W.store(underlying, timeframe, out)
    record_audit(db, action="decision.weights_fit", user_id=admin.user.id, request=request,
                 details={"underlying": underlying, "timeframe": timeframe,
                          "usable": out["usable"], "data_source": r.source})
    db.commit()
    return W.public(out) or {}
