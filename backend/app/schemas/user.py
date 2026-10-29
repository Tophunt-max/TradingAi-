from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.models.user import UserRole
from app.schemas.common import ApiModel


class UserOut(ApiModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    role: UserRole
    is_verified: bool
    created_at: datetime
    last_login_at: datetime | None


class UserUpdate(ApiModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)


class PreferencesOut(ApiModel):
    theme: Literal["dark", "light", "system"]
    language: Literal["en", "hi", "hinglish"]
    timezone: str
    reduced_motion: bool
    haptics_enabled: bool
    notifications: dict[str, Any]


class PreferencesUpdate(ApiModel):
    theme: Literal["dark", "light", "system"] | None = None
    language: Literal["en", "hi", "hinglish"] | None = None
    reduced_motion: bool | None = None
    haptics_enabled: bool | None = None
    notifications: dict[str, bool] | None = None


class AuditLogOut(ApiModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    outcome: str
    ip_address: str | None
    request_id: str | None
    details: dict[str, Any]
    created_at: datetime
