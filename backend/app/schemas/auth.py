from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import ApiModel
from app.schemas.identity import (
    normalize_email,
    normalize_phone,
    parse_identifier,
    validate_password_strength,
)
from app.schemas.user import UserOut


class RegisterRequest(ApiModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str | None = None
    phone: str | None = None
    password: str
    accept_risk_disclosure: bool = Field(
        description="User acknowledges trading risk; no profits are guaranteed."
    )

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Name is too short.")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return normalize_email(v) if v else None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v) if v else None

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password_strength(v)

    @model_validator(mode="after")
    def _one_identifier(self) -> RegisterRequest:
        if not self.email and not self.phone:
            raise ValueError("Provide an email address or a mobile number.")
        if not self.accept_risk_disclosure:
            raise ValueError("You must acknowledge the risk disclosure to register.")
        return self


class IdentifierMixin(ApiModel):
    identifier: str = Field(min_length=3, max_length=320, description="Email or mobile")

    @field_validator("identifier")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return parse_identifier(v).value


class LoginRequest(IdentifierMixin):
    password: str = Field(min_length=1, max_length=128)
    device_name: str | None = Field(default=None, max_length=120)


class VerifyRequest(IdentifierMixin):
    code: str = Field(pattern=r"^\d{6}$")
    device_name: str | None = Field(default=None, max_length=120)


class ResendVerificationRequest(IdentifierMixin):
    pass


class ForgotPasswordRequest(IdentifierMixin):
    pass


class ResetPasswordRequest(IdentifierMixin):
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password_strength(v)


class ChangePasswordRequest(ApiModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password_strength(v)


class RefreshRequest(ApiModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenPair(ApiModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (OAuth token type)
    expires_in: int
    user: UserOut


class RegisterResponse(ApiModel):
    user: UserOut
    verification_required: bool = True
    verification_channel: Literal["EMAIL", "SMS"]


class SessionOut(ApiModel):
    id: uuid.UUID
    device_name: str | None
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    current: bool = False
