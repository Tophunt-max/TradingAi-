from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbDep, SettingsDep
from app.core.time import utcnow

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

    # Workers and broker integrations do not exist yet (Phases 3, 5, 6, 8).
    for name in ("market_data_worker", "ai_worker", "execution_worker", "broker"):
        components.append(ComponentHealth(name=name, status="not_configured",
                                          detail="Not implemented in this build."))

    overall = "down" if any(c.name == "database" and c.status == "down" for c in components) \
        else "ok"
    report = HealthReport(status=overall, checked_at=utcnow().isoformat(), components=components)
    return JSONResponse(status_code=200 if overall != "down" else 503,
                        content=report.model_dump())
