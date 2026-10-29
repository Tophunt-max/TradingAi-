"""Builds the configured MarketDataProvider and maps provider errors to API errors."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.core.config import MarketDataMode, Settings
from app.core.errors import AppError, NotFoundError, PermissionDeniedError, ServiceUnavailableError
from app.market_data.indstocks.client import IndstocksClient, IndstocksError
from app.market_data.indstocks.provider import IndstocksProvider, InstrumentsNotSynced
from app.market_data.provider import DemoProvider, MarketDataProvider, UnknownInstrument
from app.market_data.ticks import get_tick_store

_client: IndstocksClient | None = None
# Tests replace this to inject a mock transport.
client_factory: Callable[[Settings], IndstocksClient] | None = None


def get_client(settings: Settings) -> IndstocksClient:
    global _client
    if client_factory is not None:
        return client_factory(settings)
    if _client is None:
        token = settings.indstocks_access_token
        _client = IndstocksClient(
            base_url=settings.indstocks_base_url,
            token=token.get_secret_value() if token else None,
            timeout=settings.indstocks_timeout_seconds,
        )
    return _client


def build_provider(db: Session, settings: Settings) -> MarketDataProvider:
    if settings.market_data_mode == MarketDataMode.DEMO:
        return DemoProvider()
    if settings.market_data_mode == MarketDataMode.INDSTOCKS:
        return IndstocksProvider(
            db, settings, get_client(settings), ticks=get_tick_store(settings.redis_url)
        )
    raise ServiceUnavailableError(
        "No authorised market-data provider is configured.", code="MARKET_DATA_UNAVAILABLE"
    )


def get_provider(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> MarketDataProvider:
    if settings.market_data_mode == MarketDataMode.INDSTOCKS:
        allowed = {e.lower() for e in settings.indstocks_data_user_emails}
        if (ctx.user.email or "").lower() not in allowed:
            raise PermissionDeniedError(
                "Live data comes from the account owner's INDstocks login and is limited to "
                "approved users.",
                code="MARKET_DATA_NOT_LICENSED",
            )
    return build_provider(db, settings)


ProviderDep = Annotated[MarketDataProvider, Depends(get_provider)]


@contextmanager
def provider_errors() -> Iterator[None]:
    """Translate provider failures into the API's error envelope."""
    try:
        yield
    except UnknownInstrument as exc:
        raise NotFoundError(f"Unknown instrument '{exc.args[0]}'.") from exc
    except InstrumentsNotSynced as exc:
        raise ServiceUnavailableError(str(exc), code="INSTRUMENTS_NOT_SYNCED") from exc
    except IndstocksError as exc:
        codes: dict[str, tuple[str, str, int, dict[str, Any] | None]] = {
            "AUTH": (
                "PROVIDER_AUTH_FAILED",
                "INDstocks rejected the access token. Generate a new token (they last 24 hours).",
                503,
                None,
            ),
            "RATE_LIMIT": (
                "PROVIDER_RATE_LIMITED",
                "INDstocks rate limit reached. Try again shortly.",
                503,
                {"Retry-After": "5"},
            ),
            "UNAVAILABLE": (
                "PROVIDER_UNAVAILABLE",
                "INDstocks is temporarily unavailable.",
                503,
                None,
            ),
            "NOT_CONFIGURED": (
                "PROVIDER_NOT_CONFIGURED",
                "INDSTOCKS_ACCESS_TOKEN is not set.",
                503,
                None,
            ),
            "BAD_REQUEST": ("PROVIDER_BAD_REQUEST", "INDstocks rejected the request.", 502, None),
        }
        code, msg, status, headers = codes.get(exc.kind, codes["UNAVAILABLE"])
        raise AppError(
            msg,
            code=code,
            status_code=status,
            headers=headers,
            details={"provider_message": str(exc), "detail": exc.detail},
        ) from exc
