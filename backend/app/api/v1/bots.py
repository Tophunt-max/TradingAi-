from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select

from app.analysis import context as C
from app.analysis import pipeline
from app.analysis import regime as R
from app.api.deps import AuthDep, DbDep, SettingsDep
from app.bots import lifecycle
from app.core.errors import AppError
from app.market_data.factory import ProviderDep, get_provider
from app.models.bots import Bot, BotCreationMethod, BotEvent, BotRun, BotSignal, BotStatus
from app.schemas.bots import (
    AiDraftRequest,
    AiDraftResponse,
    BotCreate,
    BotDetail,
    BotEventOut,
    BotOut,
    BotRunOut,
    BotSignalOut,
    BotUpdate,
    StrategyOut,
    ValidationReport,
)
from app.services import bots as svc
from app.services import risk as risk_svc
from app.services.audit import record_audit
from app.strategies.catalog import STRATEGIES, TEMPLATE_ORDER
from app.strategies.conditions import mirror

router = APIRouter(prefix="/bots", tags=["bots"])
logger = logging.getLogger("app.bots")


@router.get("/strategies", response_model=list[StrategyOut])
def strategies(_: AuthDep) -> list[StrategyOut]:
    return [StrategyOut(id=k, default_entry_pe=[mirror(c) for c in STRATEGIES[k]["default_entry"]],
                        **{f: STRATEGIES[k][f] for f in StrategyOut.model_fields
                           if f not in ("id", "default_entry_pe")}) for k in TEMPLATE_ORDER]


def _draft_analysis(provider: Any, underlying: str, timeframe: str) -> dict[str, Any] | None:
    try:
        r = provider.candles(underlying, timeframe, 300)
        if len(r.data) < 80:
            return None
        ctx = C.build(r.data, intraday=True)
        trend, _ = pipeline._legacy_signals(ctx)
        return {"regime": R.detect(ctx), "trend": trend, "source": r.source,
                "timeframe": timeframe}
    except Exception:
        logger.warning("ai_draft_analysis_unavailable", extra={"underlying": underlying})
        return None


@router.post("/ai-draft", response_model=AiDraftResponse)
def ai_draft(body: AiDraftRequest, ctx: AuthDep, db: DbDep,
             provider: ProviderDep) -> AiDraftResponse:
    rs = risk_svc.get_or_create(db, ctx.user.id)
    db.commit()
    tf = body.timeframe if body.timeframe in ("5m", "15m") else "15m"
    return svc.ai_draft(body, rs, _draft_analysis(provider, body.underlying, tf))


@router.get("", response_model=list[BotOut])
def list_bots(
    ctx: AuthDep, db: DbDep,
    status_filter: Annotated[BotStatus | None, Query(alias="status")] = None,
    account_mode: Annotated[Literal["DEMO", "REAL"] | None,
                            Query(description="Defaults to the active mode.")] = None,
) -> list[BotOut]:
    from app.accounts import core as ACC

    mode = account_mode or ACC.active_mode(db, ctx.user.id)
    stmt = select(Bot).where(Bot.user_id == ctx.user.id, Bot.account_mode == mode)
    if status_filter:
        stmt = stmt.where(Bot.status == status_filter)
    return [svc.to_out(b, db) for b in db.scalars(stmt.order_by(Bot.updated_at.desc())).all()]


@router.post("", response_model=BotDetail, status_code=status.HTTP_201_CREATED)
def create_bot(body: BotCreate, ctx: AuthDep, db: DbDep, request: Request) -> BotDetail:
    bot = svc.create(db, ctx.user.id, body.config, BotCreationMethod(body.creation_method))
    record_audit(db, action="bot.created", user_id=ctx.user.id, request=request,
                 entity_type="bot", entity_id=str(bot.id),
                 details={"method": body.creation_method, "strategy": bot.strategy_id})
    db.commit()
    return svc.to_detail(bot, db)


@router.get("/{bot_id}", response_model=BotDetail)
def get_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep) -> BotDetail:
    return svc.to_detail(svc.get_owned(db, ctx.user.id, bot_id), db)


@router.put("/{bot_id}", response_model=BotDetail)
def update_bot(bot_id: uuid.UUID, body: BotUpdate, ctx: AuthDep, db: DbDep,
               request: Request) -> BotDetail:
    bot = svc.get_owned(db, ctx.user.id, bot_id)
    if bot.status not in (BotStatus.DRAFT, BotStatus.STOPPED):
        raise AppError("Stop the bot before editing it.", code="BOT_NOT_EDITABLE",
                       status_code=409)
    svc.update(db, bot, body.config)
    record_audit(db, action="bot.updated", user_id=ctx.user.id, request=request,
                 entity_type="bot", entity_id=str(bot.id),
                 details={"version": bot.current_version})
    db.commit()
    return svc.to_detail(bot, db)


@router.post("/{bot_id}/clone", response_model=BotDetail, status_code=201)
def clone_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep, request: Request) -> BotDetail:
    src = svc.get_owned(db, ctx.user.id, bot_id)
    bot = svc.clone(db, ctx.user.id, src)
    record_audit(db, action="bot.cloned", user_id=ctx.user.id, request=request,
                 entity_type="bot", entity_id=str(bot.id), details={"source": str(src.id)})
    db.commit()
    return svc.to_detail(bot, db)


@router.delete("/{bot_id}", status_code=204)
def delete_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep, request: Request,
               confirm: Annotated[bool, Query()] = False) -> None:
    bot = svc.get_owned(db, ctx.user.id, bot_id)
    if not confirm:
        raise AppError("Deletion must be confirmed with ?confirm=true.",
                       code="CONFIRMATION_REQUIRED", status_code=400)
    if bot.status in (BotStatus.ACTIVE, BotStatus.PAUSED):
        raise AppError("Stop the bot before deleting it.", code="BOT_ACTIVE", status_code=409)
    record_audit(db, action="bot.deleted", user_id=ctx.user.id, request=request,
                 entity_type="bot", entity_id=str(bot.id), details={"name": bot.name})
    db.delete(bot)
    db.commit()


def _report(db: DbDep, settings: SettingsDep, bot: Bot) -> ValidationReport:
    rs = risk_svc.get_or_create(db, bot.user_id)
    db.commit()
    cfg = svc.current_config(bot)
    return svc.validate(bot, cfg, rs, svc.market_context(db, settings, cfg),
                        paper_enabled=settings.paper_trading_enabled)


@router.post("/{bot_id}/validate", response_model=ValidationReport)
def validate_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep,
                 settings: SettingsDep) -> ValidationReport:
    return _report(db, settings, svc.get_owned(db, ctx.user.id, bot_id))


_REASONS = {"start": "Started by user in signal-only mode.", "pause": "Paused by user.",
            "resume": "Resumed by user.", "stop": "Stopped by user."}


def _lifecycle(action: str):  # type: ignore[no-untyped-def]
    def handler(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep, settings: SettingsDep,
                request: Request) -> BotDetail:
        bot = svc.get_owned(db, ctx.user.id, bot_id)
        if action in ("start", "resume") and bot.account_mode != "DEMO":
            from app.accounts import core as ACC
            from app.services.accounts import real_unavailable_reasons

            reasons = real_unavailable_reasons(
                db, settings, ACC.get_account(db, ctx.user.id, bot.account_mode))
            record_audit(db, action=f"bot.{action}", user_id=ctx.user.id, request=request,
                         entity_type="bot", entity_id=str(bot.id), outcome="REJECTED",
                         details={"account_mode": bot.account_mode, "reasons": reasons})
            db.commit()
            raise AppError("A REAL-account bot cannot start: live trading is locked.",
                           code="LIVE_TRADING_LOCKED", status_code=409,
                           details={"account_mode": bot.account_mode, "reasons": reasons})
        if action in ("start", "resume"):
            report = _report(db, settings, bot)
            if not report.can_activate:
                failed = [c.label for c in report.checks
                          if c.required_for == "ACTIVATION" and c.status == "FAIL"]
                record_audit(db, action=f"bot.{action}", user_id=ctx.user.id, request=request,
                             entity_type="bot", entity_id=str(bot.id), outcome="REJECTED",
                             details={"failed_checks": failed})
                db.commit()
                raise AppError("The bot did not pass activation checks: " + ", ".join(failed)
                               + ".", code="VALIDATION_FAILED", status_code=409,
                               details=report.model_dump(mode="json"))
        mode = "PAPER" if settings.paper_trading_enabled else "SIGNAL_ONLY"
        lifecycle.transition(db, bot, action, _REASONS[action].replace(
            "signal-only", "paper" if mode == "PAPER" else "signal-only"), mode=mode)
        record_audit(db, action=f"bot.{action}", user_id=ctx.user.id, request=request,
                     entity_type="bot", entity_id=str(bot.id), details={"name": bot.name})
        db.commit()
        return svc.to_detail(bot, db)

    handler.__name__ = f"{action}_bot"
    return handler


for _action in ("start", "pause", "resume", "stop"):
    # Starting/resuming reads market data, so it needs the data licence check.
    deps = [Depends(get_provider)] if _action in ("start", "resume") else []
    router.add_api_route(f"/{{bot_id}}/{_action}", _lifecycle(_action), methods=["POST"],
                         response_model=BotDetail, dependencies=deps,
                         responses={409: {"description": "Invalid state or failed checks"}})


@router.get("/{bot_id}/signals", response_model=list[BotSignalOut])
def bot_signals(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep,
                limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[BotSignalOut]:
    svc.get_owned(db, ctx.user.id, bot_id)
    rows = db.scalars(select(BotSignal).where(BotSignal.bot_id == bot_id)
                      .order_by(BotSignal.bar_ts.desc()).limit(limit)).all()
    return [BotSignalOut.model_validate(r) for r in rows]


@router.get("/{bot_id}/events", response_model=list[BotEventOut])
def bot_events(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep,
               limit: Annotated[int, Query(ge=1, le=200)] = 100) -> list[BotEventOut]:
    svc.get_owned(db, ctx.user.id, bot_id)
    rows = db.scalars(select(BotEvent).where(BotEvent.bot_id == bot_id)
                      .order_by(BotEvent.ts.desc()).limit(limit)).all()
    return [BotEventOut.model_validate(r) for r in rows]


@router.get("/{bot_id}/runs", response_model=list[BotRunOut])
def bot_runs(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep) -> list[BotRunOut]:
    svc.get_owned(db, ctx.user.id, bot_id)
    rows = db.scalars(select(BotRun).where(BotRun.bot_id == bot_id)
                      .order_by(BotRun.started_at.desc()).limit(50)).all()
    return [BotRunOut.model_validate(r) for r in rows]
