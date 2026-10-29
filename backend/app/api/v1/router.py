from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    admin,
    alerts,
    analysis,
    auth,
    backtests,
    bots,
    charts,
    health,
    market,
    models,
    options,
    patterns,
    portfolio,
    risk,
    system,
    users,
)

api_router = APIRouter(prefix="/api/v1")
for module in (health, system, auth, users, admin, accounts, market, charts, options, analysis,
               bots, risk, portfolio, alerts, models):
    api_router.include_router(module.router)
api_router.include_router(backtests.router)
api_router.include_router(patterns.router)
