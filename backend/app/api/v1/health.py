from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import DbDep, SettingsDep
from app.core.config import MarketDataMode, Settings
from app.core.time import utcnow
from app.market_data.factory import build_provider
from app.models.bots import WorkerHeartbeat

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger("app.health")


class ComponentHealth(BaseModel):
    name: str
    status: Literal["ok", "degraded", "down", "not_configured"]
    latency_ms: float | None = None
    detail: str | None = None


class HealthReport(BaseModel):
    status: Literal["ok", "degraded", "down"]
    checked_at: str
    components: list[ComponentHealth]


def _market_data_health(db: Session, settings: Settings) -> list[ComponentHealth]:
    if settings.market_data_mode == MarketDataMode.DEMO:
        return [ComponentHealth(name="market_data", status="degraded",
                                detail="DEMO mode: synthetic data only."),
                ComponentHealth(name="market_data_worker", status="not_configured",
                                detail="Not used in DEMO mode.")]
    if settings.market_data_mode != MarketDataMode.INDSTOCKS:
        return [ComponentHealth(name="market_data", status="down",
                                detail="No market-data provider configured.")]
    try:
        st = build_provider(db, settings).status()
    except Exception as exc:
        logger.exception("market_data_health_failed")
        return [ComponentHealth(name="market_data", status="down", detail=type(exc).__name__)]
    syncs = st.get("instrument_sync") or {}
    missing = [s for s, v in syncs.items() if not v]
    status = "down" if st["status"] != "ok" else ("degraded" if missing else "ok")
    out = [ComponentHealth(
        name="market_data", status=status,
        detail=f"INDstocks: {st.get('detail')}"
        + (f" Instrument master not synced: {', '.join(missing)}." if missing else ""))]
    hb = st.get("price_feed_heartbeat")
    if hb is None:
        out.append(ComponentHealth(name="market_data_worker", status="not_configured",
                                   detail="Price-feed worker has not reported (needs REDIS_URL)."))
    else:
        age = (utcnow() - datetime.fromisoformat(hb)).total_seconds()
        out.append(ComponentHealth(name="market_data_worker",
                                   status="ok" if age < 120 else "degraded",
                                   detail=f"Last heartbeat {int(age)}s ago."))
    return out


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=HealthReport)
def ready(db: DbDep, settings: SettingsDep) -> JSONResponse:
    components: list[ComponentHealth] = []

    start = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        components.append(ComponentHealth(name="database", status="ok",
                                          latency_ms=round((time.perf_counter() - start) * 1e3, 2)))
    except Exception:
        logger.exception("db_health_failed")
        components.append(ComponentHealth(name="database", status="down"))

    if settings.redis_url:
        start = time.perf_counter()
        try:
            import redis

            redis.Redis.from_url(settings.redis_url, socket_timeout=0.5).ping()
            components.append(ComponentHealth(
                name="redis", status="ok",
                latency_ms=round((time.perf_counter() - start) * 1e3, 2)))
        except Exception:
            components.append(ComponentHealth(name="redis", status="down"))
    else:
        components.append(ComponentHealth(name="redis", status="not_configured",
                                          detail="In-memory rate limiting (dev only)."))

    components.extend(_market_data_health(db, settings))

    hb = db.get(WorkerHeartbeat, "bot_runner")
    if hb is None:
        components.append(ComponentHealth(name="bot_worker", status="not_configured",
                                          detail="No bot worker has reported yet."))
    else:
        age = (utcnow() - hb.last_seen).total_seconds()
        components.append(ComponentHealth(
            name="bot_worker", status="ok" if age < 120 else "degraded",
            detail=f"Last pass {int(age)}s ago; {hb.info.get('runs', 0)} running bot(s)."))

    # Not built yet (Phases 6 and 8).
    for name in ("execution_worker", "broker"):
        components.append(ComponentHealth(name=name, status="not_configured",
                                          detail="Not implemented in this build."))

    overall = "down" if any(c.name == "database" and c.status == "down" for c in components) \
        else "ok"
    report = HealthReport(status=overall, checked_at=utcnow().isoformat(), components=components)
    return JSONResponse(status_code=200 if overall != "down" else 503,
                        content=report.model_dump())
