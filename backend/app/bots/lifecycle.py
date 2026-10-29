"""Bot state machine.

    DRAFT/STOPPED --start--> ACTIVE --pause--> PAUSED --resume--> ACTIVE
    ACTIVE/PAUSED --stop--> STOPPED

Transitions are compare-and-set updates on the bots row, so a concurrent
request or the worker can never move a bot from an unexpected state.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.time import utcnow
from app.models.bots import Bot, BotEvent, BotRun, BotStatus

TRANSITIONS: dict[str, tuple[set[BotStatus], BotStatus]] = {
    "start": ({BotStatus.DRAFT, BotStatus.STOPPED}, BotStatus.ACTIVE),
    "pause": ({BotStatus.ACTIVE}, BotStatus.PAUSED),
    "resume": ({BotStatus.PAUSED}, BotStatus.ACTIVE),
    "stop": ({BotStatus.ACTIVE, BotStatus.PAUSED}, BotStatus.STOPPED),
}


def open_run(db: Session, bot_id) -> BotRun | None:  # type: ignore[no-untyped-def]
    return db.scalar(select(BotRun).where(BotRun.bot_id == bot_id, BotRun.status != "ENDED")
                     .order_by(BotRun.started_at.desc()).limit(1))


def latest_run(db: Session, bot_id) -> BotRun | None:  # type: ignore[no-untyped-def]
    return db.scalar(select(BotRun).where(BotRun.bot_id == bot_id)
                     .order_by(BotRun.started_at.desc()).limit(1))


def event(db: Session, bot: Bot, run: BotRun | None, level: str, kind: str, message: str,
          **data: object) -> None:
    db.add(BotEvent(bot_id=bot.id, run_id=run.id if run else None, ts=utcnow(), level=level,
                    kind=kind, message=message[:500], data=data))


def transition(db: Session, bot: Bot, action: str, reason: str,
               mode: str = "SIGNAL_ONLY") -> BotRun | None:
    allowed, target = TRANSITIONS[action]
    now = utcnow()
    res = db.execute(
        update(Bot).where(Bot.id == bot.id, Bot.status.in_(allowed))
        .values(status=target, status_reason=reason[:200], status_changed_at=now,
                updated_at=now)
        .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.refresh(bot)
        raise ConflictError(
            f"Cannot {action} a bot that is {bot.status.value}.", code="INVALID_BOT_STATE",
            details={"status": bot.status.value, "allowed_from": sorted(s.value for s in allowed)})
    db.refresh(bot)
    run = open_run(db, bot.id)
    if action == "start":
        if run is not None:  # defensive: never two open runs
            run.status, run.ended_at, run.end_reason = "ENDED", now, "superseded"
        run = BotRun(bot_id=bot.id, user_id=bot.user_id, config_version=bot.current_version,
                     mode=mode, status="RUNNING", started_at=now)
        db.add(run)
        db.flush()
    elif run is not None:
        if action == "pause":
            run.status, run.lease_owner, run.lease_until = "PAUSED", None, None
        elif action == "resume":
            run.status, run.consecutive_errors = "RUNNING", 0
        elif action == "stop":
            run.status, run.ended_at, run.end_reason = "ENDED", now, reason[:200]
            run.lease_owner, run.lease_until = None, None
    event(db, bot, run, "INFO", "STATE", f"{action.capitalize()}: {reason}",
          status=target.value)
    return run
