from fastapi import APIRouter

from app.api.v1 import (
    admin,
    alerts,
    analysis,
    auth,
    bots,
    charts,
    health,
    market,
    models,
    options,
    portfolio,
    risk,
    system,
    users,
)

api_router = APIRouter(prefix="/api/v1")
for module in (health, system, auth, users, admin, market, charts, options, analysis, bots,
               risk, portfolio, alerts, models):
    api_router.include_router(module.router)
