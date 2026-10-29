"""Bot evaluation engine (SIGNAL_ONLY until the execution engine exists).

One pass (`run_once`) claims each RUNNING bot run with a lease, fetches closed
candles, and evaluates the bot's configured entry conditions on the newest
closed candle exactly once. Signals are idempotent per (run, candle), so a
worker restart or a second worker can never record the same signal twice.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import registry
from app.analysis import context as C
from app.analysis import regime as R
from app.bots import lifecycle
from app.core.config import MarketDataMode, Settings
from app.core.time import IST
from app.market_data.provider import MarketDataProvider
from app.models.bots import Bot, BotConfigVersion, BotRun, BotSignal, BotStatus, WorkerHeartbeat
from app.models.execution import WorkerLease
from app.models.user import User
from app.schemas.bots import BotConfig
from app.services import risk as risk_svc
from app.strategies.conditions import Evaluator, describe

logger = logging.getLogger("app.bot_engine")

WORKER_NAME = "bot_runner"
LEASE_SECONDS = 60
MAX_CONSECUTIVE_ERRORS = 10
CANDLES = 300
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}

ProviderFactory = Callable[[Session], MarketDataProvider]


def claim(db: Session, run_id: uuid.UUID, owner: str, now: datetime) -> bool:
    res = db.execute(
        update(BotRun)
        .where(BotRun.id == run_id, BotRun.status == "RUNNING",
               or_(BotRun.lease_owner.is_(None), BotRun.lease_owner == owner,
                   BotRun.lease_until < now))
        .values(lease_owner=owner, lease_until=now + timedelta(seconds=LEASE_SECONDS))
        .execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def heartbeat(db: Session, owner: str, now: datetime, info: dict[str, Any]) -> None:
    hb = db.get(WorkerHeartbeat, WORKER_NAME)
    if hb is None:
        db.add(WorkerHeartbeat(name=WORKER_NAME, instance=owner, last_seen=now, info=info))
    else:
        hb.instance, hb.last_seen, hb.info = owner, now, info
    db.commit()


def _day_start_utc(bar_ts: datetime) -> datetime:
    d = bar_ts.astimezone(IST).date()
    return datetime.combine(d, time(0, 0), IST)


def _hhmm(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def _suppress_reason(db: Session, bot: Bot, cfg: BotConfig, rs: Any, ctx: C.Ctx,
                     bar_ts: datetime, quality: str) -> str | None:
    if quality == "FAIL":
        return "Market-data quality check failed for this candle."
    if rs.kill_switch_engaged:
        return "Kill switch is engaged."
    local = bar_ts.astimezone(IST).time()
    if not (_hhmm(cfg.session.start_ist) <= local < _hhmm(cfg.session.end_ist)):
        return (f"Outside the bot's session {cfg.session.start_ist}–{cfg.session.end_ist} IST "
                f"(candle {local:%H:%M}).")
    f = cfg.filters
    rvol, a = ctx.s["rvol20"][-1], ctx.s["atr14"][-1]
    if f.min_relative_volume is not None and (rvol is None or rvol < f.min_relative_volume):
        return f"Relative volume {rvol if rvol is None else round(rvol, 2)} below filter " \
               f"{f.min_relative_volume}."
    if f.max_atr_pct is not None and a is not None and a / ctx.c[-1] * 100 > f.max_atr_pct:
        return f"ATR {a / ctx.c[-1] * 100:.2f}% above filter {f.max_atr_pct}%."
    if cfg.session.cooldown_minutes:
        recent = db.scalar(select(BotSignal.bar_ts).where(
            BotSignal.bot_id == bot.id,
            BotSignal.bar_ts > bar_ts - timedelta(minutes=cfg.session.cooldown_minutes))
            .limit(1))
        if recent is not None:
            return f"Cooldown: last signal less than {cfg.session.cooldown_minutes} min ago."
    day0 = _day_start_utc(bar_ts)
    bot_today = db.scalar(select(func.count()).select_from(BotSignal).where(
        BotSignal.bot_id == bot.id, BotSignal.bar_ts >= day0)) or 0
    if bot_today >= cfg.risk.max_entries_per_day:
        return f"Bot daily limit reached ({bot_today}/{cfg.risk.max_entries_per_day})."
    # Global across every bot of this user in the same account mode.
    user_today = db.scalar(select(func.count()).select_from(BotSignal)
                           .join(BotRun, BotRun.id == BotSignal.run_id).where(
        BotSignal.user_id == bot.user_id, BotRun.account_mode == bot.account_mode,
        BotSignal.bar_ts >= day0)) or 0
    if user_today >= rs.max_entries_per_day:
        return (f"Global daily entry limit reached ({user_today}/{rs.max_entries_per_day}), "
                "shared by all your bots.")
    return None


def evaluate_run(db: Session, provider: MarketDataProvider, run: BotRun, bot: Bot,
                 cfg: BotConfig, now: datetime, settings: Settings | None = None) -> str:
    r = provider.candles(cfg.instrument.underlying, cfg.timeframe, CANDLES)
    run.last_heartbeat_at = now
    if len(r.data) < 60:
        raise RuntimeError(f"Only {len(r.data)} closed candles available.")
    bar_ts = r.data[-1]["ts"]
    if run.last_bar_ts is not None and bar_ts <= run.last_bar_ts:
        return "NO_NEW_BAR"
    run.last_bar_ts = bar_ts
    ctx = C.build(r.data, intraday=True)
    side = cfg.instrument.option_type

    def probability() -> dict[str, float] | None:
        mv = registry.serving(db, cfg.instrument.underlying, cfg.timeframe)
        if mv is None or r.quality.status == "FAIL":
            return None
        return registry.predict(mv, ctx, r.source).get("probabilities")

    ev = Evaluator(ctx, side, probability)
    conds = [c.model_dump() for c in cfg.entry.conditions]
    results = [{"label": describe(c), "met": ev.evaluate(c)} for c in conds]
    mets = [x["met"] is True for x in results]
    fired = bool(conds) and (all(mets) if cfg.entry.logic == "ALL" else any(mets))
    if not fired:
        return "NO_SIGNAL"

    rs = risk_svc.get_or_create(db, bot.user_id)
    reason = _suppress_reason(db, bot, cfg, rs, ctx, bar_ts, r.quality.status)
    price = ctx.c[-1]
    if reason:
        lifecycle.event(db, bot, run, "WARN", "SUPPRESSED",
                        f"{side} conditions met at {price:.2f}, signal suppressed: {reason}",
                        bar_ts=bar_ts.isoformat(), conditions=results)
        return "SUPPRESSED"
    reg = R.detect(ctx)
    sig = BotSignal(bot_id=bot.id, run_id=run.id, user_id=bot.user_id,
                    config_version=run.config_version, bar_ts=bar_ts, side=side,
                    underlying=cfg.instrument.underlying, timeframe=cfg.timeframe,
                    price=Decimal(str(round(price, 4))), regime=reg["label"] if reg else None,
                    conditions=results, data_source=r.source)
    db.add(sig)
    db.flush()
    run.signals_count += 1
    note = " (DEMO data)" if r.is_demo else ""
    if run.mode != "PAPER" or run.account_mode != "DEMO" or settings is None \
            or not settings.paper_trading_enabled:
        lifecycle.event(db, bot, run, "INFO", "SIGNAL",
                        f"{side} entry signal at {price:.2f}{note}. Signal-only: no order placed.",
                        bar_ts=bar_ts.isoformat(), conditions=results)
        return "SIGNAL"
    order = paper_entry_for_signal(db, settings, provider, bot, cfg, sig, now, run)
    if order.status == "REJECTED":
        lifecycle.event(db, bot, run, "WARN", "ORDER",
                        f"{side} signal at {price:.2f}{note}; paper entry rejected: "
                        f"{order.reject_reason}", order_id=str(order.id))
    else:
        lifecycle.event(db, bot, run, "INFO", "ORDER",
                        f"{side} signal at {price:.2f}{note}; paper order {order.status.lower()}: "
                        f"BUY {order.quantity} {order.trading_symbol}.", order_id=str(order.id))
    return "SIGNAL"


def paper_entry_for_signal(db: Session, settings: Settings, provider: MarketDataProvider,
                           bot: Bot, cfg: BotConfig, sig: BotSignal, now: datetime,
                           run: BotRun | None = None) -> Any:
    """Entry for a signal, in the account the run was started in (loaded server-side)."""
    from app.execution import service as EX
    from app.execution.router import load_account

    account = load_account(db, run.account_id) if run is not None \
        else lifecycle.run_account(db, bot)
    cache = EX.ChainCache(provider, settings)
    try:
        with db.begin_nested():
            order = EX.place_entry(
                db, settings, cache, user_id=bot.user_id, now=now,
                option_type=cfg.instrument.option_type, underlying=cfg.instrument.underlying,
                stop=cfg.exit.stop_loss.model_dump(mode="json"),
                target=cfg.exit.target.model_dump(mode="json"),
                trailing=cfg.exit.trailing_stop.model_dump(mode="json")
                if cfg.exit.trailing_stop else None,
                client_order_id=f"SIG-{sig.id.hex}",
                strike_selection=cfg.instrument.strike_selection,
                expiry_selection=cfg.instrument.expiry_selection, order_type=cfg.order.order_type,
                limit_offset_pct=cfg.order.limit_offset_pct, bot_id=bot.id, signal_id=sig.id,
                bot_risk_per_trade=cfg.risk.risk_per_trade,
                bot_square_off=cfg.exit.square_off_time_ist, account=account)
            EX.fill_now(db, settings, cache, order, now)
    except EX.ExecutionError as exc:
        raise RuntimeError(f"Paper entry failed: {exc.message}") from exc
    return order


EXECUTION_LEASE = "execution"


def claim_singleton(db: Session, name: str, owner: str, now: datetime) -> bool:
    lease = db.get(WorkerLease, name)
    if lease is None:
        db.add(WorkerLease(name=name, owner=owner,
                           lease_until=now + timedelta(seconds=LEASE_SECONDS)))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False
    res = db.execute(update(WorkerLease).where(
        WorkerLease.name == name, or_(WorkerLease.owner == owner, WorkerLease.lease_until < now))
        .values(owner=owner, lease_until=now + timedelta(seconds=LEASE_SECONDS))
        .execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def execution_pass(db: Session, settings: Settings, owner: str,
                   provider_factory: ProviderFactory, now: datetime) -> dict[str, int] | None:
    from app.execution import service as EX

    if not claim_singleton(db, EXECUTION_LEASE, owner, now):
        return None
    stats = EX.run_pass(db, settings, provider_factory(db), now)
    hb = db.get(WorkerHeartbeat, "execution")
    if hb is None:
        db.add(WorkerHeartbeat(name="execution", instance=owner, last_seen=now, info=stats))
    else:
        hb.instance, hb.last_seen, hb.info = owner, now, stats
    db.commit()
    return stats


def _licensed(db: Session, settings: Settings, user_id: uuid.UUID) -> bool:
    if settings.market_data_mode != MarketDataMode.INDSTOCKS:
        return True
    email = db.scalar(select(User.email).where(User.id == user_id)) or ""
    return email.lower() in {e.lower() for e in settings.indstocks_data_user_emails}


def run_once(db: Session, settings: Settings, owner: str, provider_factory: ProviderFactory,
             now: datetime) -> dict[str, int]:
    stats: dict[str, int] = {}
    run_ids = db.scalars(select(BotRun.id).join(Bot, Bot.id == BotRun.bot_id).where(
        BotRun.status == "RUNNING", Bot.status == BotStatus.ACTIVE)).all()
    provider: MarketDataProvider | None = None
    for run_id in run_ids:
        if not claim(db, run_id, owner, now):
            stats["LEASED_ELSEWHERE"] = stats.get("LEASED_ELSEWHERE", 0) + 1
            continue
        run = db.get(BotRun, run_id)
        bot = db.get(Bot, run.bot_id) if run else None
        if run is None or bot is None or bot.status != BotStatus.ACTIVE:
            continue
        try:
            if not _licensed(db, settings, bot.user_id):
                raise PermissionError("Market data is not licensed for this user.")
            ver = db.scalar(select(BotConfigVersion).where(
                BotConfigVersion.bot_id == bot.id,
                BotConfigVersion.version == run.config_version))
            cfg = BotConfig.model_validate(ver.config)  # type: ignore[union-attr]
            provider = provider or provider_factory(db)
            outcome = evaluate_run(db, provider, run, bot, cfg, now, settings)
            run.consecutive_errors = 0
            db.commit()
        except IntegrityError:
            db.rollback()
            outcome = "DUPLICATE"
        except Exception as exc:  # one bot's failure must not stop the others
            db.rollback()
            outcome = "ERROR"
            run = db.get(BotRun, run_id)
            bot = db.get(Bot, run.bot_id)  # type: ignore[union-attr]
            msg = f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"[:500]
            logger.warning("bot_eval_failed", extra={"bot_id": str(bot.id), "error": msg})
            prev = run.last_error
            run.consecutive_errors += 1
            run.last_error, run.last_error_at, run.last_heartbeat_at = msg, now, now
            if prev != msg or run.consecutive_errors in (1, MAX_CONSECUTIVE_ERRORS):
                lifecycle.event(db, bot, run, "ERROR", "ERROR", msg)
            if run.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                lifecycle.transition(db, bot, "pause",
                                     f"Paused automatically after {MAX_CONSECUTIVE_ERRORS} "
                                     "consecutive errors.")
            db.commit()
        stats[outcome] = stats.get(outcome, 0) + 1
    heartbeat(db, owner, now, {"runs": len(run_ids), **stats})
    if settings.paper_trading_enabled:
        try:
            execution_pass(db, settings, owner, provider_factory, now)
        except Exception:
            db.rollback()
            logger.exception("execution_pass_failed")
    return stats
