"""Import all models so Alembic autogenerate and metadata.create_all see them."""

from app.models.accounts import Account, LedgerEntry, LiveAuthorization
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
    BotEvent,
    BotRun,
    BotSignal,
    BotStatus,
    RiskSettings,
    TradingMode,
    WorkerHeartbeat,
)
from app.models.brokers import BrokerChargeReconciliation, BrokerConnection, BrokerSnapshot
from app.models.execution import (
    DailyRiskState,
    Fill,
    Order,
    OrderEvent,
    PaperBrokerFill,
    PaperBrokerOrder,
    Position,
    WorkerLease,
)
from app.models.instruments import Instrument, InstrumentSync
from app.models.intelligence import (
    CompanyProfile,
    CorporateAction,
    DecisionRecord,
    FundamentalReport,
    MarketEvent,
    ModelDriftReport,
)
from app.models.ml import ModelVersion, Prediction
from app.models.reports import DailyReport
from app.models.user import User, UserPreferences, UserRole

__all__ = [
    "BrokerChargeReconciliation",
    "BrokerConnection",
    "BrokerSnapshot",
    "DailyReport",
    "CompanyProfile",
    "CorporateAction",
    "DecisionRecord",
    "FundamentalReport",
    "MarketEvent",
    "ModelDriftReport",
    "Account",
    "LedgerEntry",
    "LiveAuthorization",
    "AuditLog",
    "ModelVersion",
    "Prediction",
    "DailyRiskState",
    "Fill",
    "Instrument",
    "Order",
    "OrderEvent",
    "PaperBrokerFill",
    "PaperBrokerOrder",
    "Position",
    "WorkerLease",
    "InstrumentSync",
    "Bot",
    "BotConfigVersion",
    "BotCreationMethod",
    "BotEvent",
    "BotRun",
    "BotSignal",
    "WorkerHeartbeat",
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
