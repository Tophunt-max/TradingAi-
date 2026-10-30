"""Environment-based configuration.

All settings come from environment variables (or a local `.env` file in
development). Secrets are never hard-coded for non-development environments.
"""

from __future__ import annotations

from decimal import Decimal
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
    # INDstocks (INDmoney) official API, using the account owner's access token.
    INDSTOCKS = "INDSTOCKS"


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

    # INDstocks market data (https://api-docs.indstocks.com). Secrets stay server-side.
    indstocks_base_url: str = "https://api.indstocks.com"
    indstocks_ws_url: str = "wss://ws-prices.indstocks.com/api/v1/ws/prices"
    indstocks_access_token: SecretStr | None = None
    indstocks_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    # The data comes from one person's brokerage account. Only these app users
    # (comma-separated emails) may see it; others get 403. Empty = nobody.
    indstocks_data_user_emails: Annotated[list[str], NoDecode] = []
    # Optional, verify before use: REST quote codes for indices, e.g.
    # "NIFTY=NSE_123,BANKNIFTY=NSE_456". Undocumented by INDstocks, so empty by default.
    indstocks_index_quote_codes: str = ""
    # Quotes older than this during market hours are flagged STALE.
    stale_quote_seconds: int = Field(default=60, ge=5, le=3600)
    # Exchange holidays (YYYY-MM-DD, comma-separated). Not bundled: must be maintained.
    market_holidays: Annotated[list[str], NoDecode] = []

    # Bot worker. Production runs `python -m app.workers.bot_runner` as its own
    # process; the embedded thread is a development convenience only.
    bot_worker_embedded: bool = False
    bot_worker_interval_seconds: float = Field(default=15.0, ge=1, le=300)

    # Paper trading simulator.
    paper_brokerage_per_order: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    paper_slippage_ticks: int = Field(default=1, ge=0, le=20)
    paper_max_spread_pct: float = Field(default=5.0, gt=0, le=50)
    # 0 = fill the whole quantity at once; N = at most N units per matching pass.
    paper_max_fill_qty_per_pass: int = Field(default=0, ge=0)
    # DEMO chains carry no exchange lot size; paper trades on DEMO data use this
    # clearly labelled placeholder (1 unit per lot), never a guessed exchange value.
    demo_lot_size: int = Field(default=1, ge=1, le=1)
    # The DEMO market never closes; set false to exercise session rules on DEMO data.
    demo_market_always_open: bool = True

    # Fundamentals: the latest period older than this many days is flagged STALE.
    fundamentals_stale_days: int = Field(default=150, ge=30, le=730)
    # Event-risk gate window around an event's time (minutes).
    event_risk_before_minutes: int = Field(default=120, ge=0, le=7 * 24 * 60)
    event_risk_after_minutes: int = Field(default=60, ge=0, le=7 * 24 * 60)
    # Learning: a fill this far (percent of the reference price) beyond the reference
    # price is attributed as EXECUTION_SLIPPAGE.
    learning_slippage_threshold_pct: float = Field(default=1.0, gt=0, le=50)

    # Verification-code delivery: "console" (development only) or "none".
    otp_delivery: str = "console"

    @field_validator("cors_origins", "indstocks_data_user_emails", "market_holidays",
                     mode="before")
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
        if (self.market_data_mode == MarketDataMode.INDSTOCKS and not self.is_dev_like
                and not self.indstocks_access_token):
            raise ValueError(
                "INDSTOCKS_ACCESS_TOKEN is required when MARKET_DATA_MODE=INDSTOCKS.")
        if hardened and self.bot_worker_embedded:
            raise ValueError("BOT_WORKER_EMBEDDED is for development only; run the worker "
                             "as its own process.")
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
