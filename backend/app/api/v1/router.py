from fastapi import APIRouter

from app.api.v1 import admin, auth, health, market, system, users

api_router = APIRouter(prefix="/api/v1")
for module in (health, system, auth, users, admin, market):
    api_router.include_router(module.router)
