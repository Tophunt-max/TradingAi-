"""Import all models so Alembic autogenerate and metadata.create_all see them."""

from app.models.audit import AuditLog
from app.models.auth import (
    RefreshToken,
    UserSession,
    VerificationChannel,
    VerificationCode,
    VerificationPurpose,
)
from app.models.bots import (
    Bot,
    BotConfigVersion,
    BotCreationMethod,
    BotStatus,
    RiskSettings,
    TradingMode,
)
from app.models.instruments import Instrument, InstrumentSync
from app.models.ml import ModelVersion, Prediction
from app.models.user import User, UserPreferences, UserRole

__all__ = [
    "AuditLog",
    "ModelVersion",
    "Prediction",
    "Instrument",
    "InstrumentSync",
    "Bot",
    "BotConfigVersion",
    "BotCreationMethod",
    "BotStatus",
    "RiskSettings",
    "TradingMode",
    "RefreshToken",
    "User",
    "UserPreferences",
    "UserRole",
    "UserSession",
    "VerificationChannel",
    "VerificationCode",
    "VerificationPurpose",
]
