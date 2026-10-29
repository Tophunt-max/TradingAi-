from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.request_context import reset_request_id, set_request_id

_REQ_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
access_log = logging.getLogger("app.access")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        if settings.bot_worker_embedded:
            from app.workers import bot_runner

            bot_runner.start_embedded(settings.bot_worker_interval_seconds)
        yield
        if settings.bot_worker_embedded:
            from app.workers import bot_runner

            bot_runner.stop_embedded()

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        version="0.1.0",
        description=(
            "QuantAI F&O backend. Analysis tools only; no guaranteed profits. "
            "Live trading is disabled in this build."
        ),
        docs_url="/docs" if settings.is_dev_like else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_dev_like else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _REQ_ID.match(incoming) else uuid.uuid4().hex
        token = set_request_id(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            reset_request_id(token)
            raise
        elapsed = round((time.perf_counter() - start) * 1e3, 2)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if not settings.is_dev_like:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        access_log.info(
            "request",
            extra={"method": request.method, "path": request.url.path,
                   "status": response.status_code, "duration_ms": elapsed},
        )
        reset_request_id(token)
        return response

    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
