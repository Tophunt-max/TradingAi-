"""Environment-based configuration.

All settings come from environment variables (or a local `.env` file in
development). Secrets are never hard-coded for non-development environments.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEV_SECRET = "dev-only-insecure-secret-change-me-0000000000000000"  # noqa: S105 (dev placeholder, rejected outside dev)


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PAPER = "paper"
    PRODUCTION = "production"


class MarketDataMode(StrEnum):
    # Synthetic, clearly labelled data. Never used for trading decisions.
    DEMO = "DEMO"
    # No provider configured: market endpoints return 503.
    DISABLED = "DISABLED"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "QuantAI F&O API"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False

    # Database / cache
    database_url: str = "sqlite:///./quantai_dev.db"
    database_echo: bool = False
    redis_url: str | None = None

    # Security
    secret_key: SecretStr = SecretStr(_DEV_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    otp_ttl_minutes: int = Field(default=10, ge=1, le=30)
    otp_max_attempts: int = Field(default=5, ge=1, le=10)
    max_failed_logins: int = Field(default=5, ge=3, le=20)
    lockout_minutes: int = Field(default=15, ge=1, le=1440)
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:8081",
        "http://localhost:19006",
    ]

    # Rate limits (requests per window, per client IP and route)
    auth_rate_limit: int = 10
    auth_rate_window_seconds: int = 60

    # Market data & trading
    market_data_mode: MarketDataMode = MarketDataMode.DEMO
    paper_trading_enabled: bool = True
    # Live trading is hard-locked: no verified broker adapter exists yet.
    live_trading_enabled: bool = False

    # Verification-code delivery: "console" (development only) or "none".
    otp_delivery: str = "console"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _enforce_safety(self) -> Settings:
        hardened = self.environment in {
            Environment.STAGING,
            Environment.PAPER,
            Environment.PRODUCTION,
        }
        if hardened:
            if self.secret_key.get_secret_value() == _DEV_SECRET:
                raise ValueError("SECRET_KEY must be set outside development/test.")
            if len(self.secret_key.get_secret_value()) < 32:
                raise ValueError("SECRET_KEY must be at least 32 characters.")
            if self.database_url.startswith("sqlite"):
                raise ValueError("SQLite is not allowed outside development/test.")
            if self.otp_delivery == "console":
                raise ValueError("Console OTP delivery is only allowed in development/test.")
            if self.debug:
                raise ValueError("DEBUG must be off outside development/test.")
        if self.live_trading_enabled:
            raise ValueError(
                "LIVE_TRADING_ENABLED cannot be true: no verified broker adapter is "
                "implemented in this build."
            )
        return self

    @property
    def is_dev_like(self) -> bool:
        return self.environment in {Environment.DEVELOPMENT, Environment.TEST}


@lru_cache
def get_settings() -> Settings:
    return Settings()
