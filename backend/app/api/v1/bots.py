from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep
from app.core.errors import AppError
from app.models.bots import Bot, BotCreationMethod, BotStatus
from app.schemas.bots import (
    AiDraftRequest,
    AiDraftResponse,
    BotCreate,
    BotDetail,
    BotOut,
    BotUpdate,
    StrategyOut,
    ValidationReport,
)
from app.services import bots as svc
from app.services import risk as risk_svc
from app.services.audit import record_audit
from app.strategies.catalog import STRATEGIES, TEMPLATE_ORDER

router = APIRouter(prefix="/bots", tags=["bots"])


@router.get("/strategies", response_model=list[StrategyOut])
def strategies(_: AuthDep) -> list[StrategyOut]:
    return [StrategyOut(id=k, **{f: STRATEGIES[k][f] for f in StrategyOut.model_fields
                                 if f != "id"}) for k in TEMPLATE_ORDER]


@router.post("/ai-draft", response_model=AiDraftResponse)
def ai_draft(body: AiDraftRequest, ctx: AuthDep, db: DbDep) -> AiDraftResponse:
    rs = risk_svc.get_or_create(db, ctx.user.id)
    db.commit()
    return svc.ai_draft(body, rs)


@router.get("", response_model=list[BotOut])
def list_bots(
    ctx: AuthDep, db: DbDep,
    status_filter: Annotated[BotStatus | None, Query(alias="status")] = None,
) -> list[BotOut]:
    stmt = select(Bot).where(Bot.user_id == ctx.user.id)
    if status_filter:
        stmt = stmt.where(Bot.status == status_filter)
    return [svc.to_out(b) for b in db.scalars(stmt.order_by(Bot.updated_at.desc())).all()]


@router.post("", response_model=BotDetail, status_code=status.HTTP_201_CREATED)
def create_bot(body: BotCreate, ctx: AuthDep, db: DbDep, request: Request) -> BotDetail:
    bot = svc.create(db, ctx.user.id, body.config, BotCreationMethod(body.creation_method))
    record_audit(db, action="bot.created", user_id=ctx.user.id, request=request,
                 entity_type="bot", entity_id=str(bot.id),
                 details={"method": body.creation_method, "strategy": bot.strategy_id})
    db.commit()
    return svc.to_detail(bot)


@router.get("/{bot_id}", response_model=BotDetail)
def get_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep) -> BotDetail:
    return svc.to_detail(svc.get_owned(db, ctx.user.id, bot_id))


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
    return svc.to_detail(bot)


@router.post("/{bot_id}/clone", response_model=BotDetail, status_code=201)
def clone_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep, request: Request) -> BotDetail:
    src = svc.get_owned(db, ctx.user.id, bot_id)
    bot = svc.clone(db, ctx.user.id, src)
    record_audit(db, action="bot.cloned", user_id=ctx.user.id, request=request,
                 entity_type="bot", entity_id=str(bot.id), details={"source": str(src.id)})
    db.commit()
    return svc.to_detail(bot)


@router.delete("/{bot_id}", status_code=204)
def delete_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep, request: Request,
               confirm: Annotated[bool, Query()] = False) -> None:
    bot = svc.get_owned(db, ctx.user.id, bot_id)
    if not confirm:
        raise AppError("Deletion must be confirmed with ?confirm=true.",
                       code="CONFIRMATION_REQUIRED", status_code=400)
    if bot.status == BotStatus.ACTIVE:
        raise AppError("Stop the bot before deleting it.", code="BOT_ACTIVE", status_code=409)
    record_audit(db, action="bot.deleted", user_id=ctx.user.id, request=request,
                 entity_type="bot", entity_id=str(bot.id), details={"name": bot.name})
    db.delete(bot)
    db.commit()


@router.post("/{bot_id}/validate", response_model=ValidationReport)
def validate_bot(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep) -> ValidationReport:
    bot = svc.get_owned(db, ctx.user.id, bot_id)
    rs = risk_svc.get_or_create(db, ctx.user.id)
    db.commit()
    return svc.validate(bot, svc.current_config(bot), rs)


def _lifecycle(action: str):  # type: ignore[no-untyped-def]
    def handler(bot_id: uuid.UUID, ctx: AuthDep, db: DbDep, request: Request) -> None:
        bot = svc.get_owned(db, ctx.user.id, bot_id)
        record_audit(db, action=f"bot.{action}", user_id=ctx.user.id, request=request,
                     entity_type="bot", entity_id=str(bot.id), outcome="REJECTED",
                     details={"reason": "execution_engine_unavailable"})
        db.commit()
        raise AppError(
            "Bots cannot run yet: the paper execution engine and global risk manager arrive "
            "in Phase 6. The bot stays in DRAFT.",
            code="EXECUTION_UNAVAILABLE", status_code=409,
            details={"status": bot.status.value},
        )

    handler.__name__ = f"{action}_bot"
    return handler


for _action in ("start", "pause", "resume", "stop"):
    router.add_api_route(f"/{{bot_id}}/{_action}", _lifecycle(_action), methods=["POST"],
                         status_code=200, responses={409: {"description": "Not available"}})
