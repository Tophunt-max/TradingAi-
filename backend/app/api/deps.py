from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import decode_access_token
from app.core.time import ensure_utc, utcnow
from app.db.session import get_db
from app.models.auth import UserSession
from app.models.user import User, UserRole
from app.services.otp_delivery import OtpSender, get_otp_sender

_bearer = HTTPBearer(auto_error=False)

DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
OtpSenderDep = Annotated[OtpSender, Depends(get_otp_sender)]


@dataclass
class AuthContext:
    user: User
    session: UserSession


def get_auth_context(
    db: DbDep,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    unauthenticated = AuthenticationError(
        "Authentication required.", headers={"WWW-Authenticate": "Bearer"}
    )
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthenticated
    try:
        payload = decode_access_token(settings, creds.credentials)
    except jwt.PyJWTError as exc:
        raise unauthenticated from exc

    try:
        session = db.get(UserSession, uuid.UUID(payload["sid"]))
    except ValueError as exc:
        raise unauthenticated from exc
    if (
        session is None
        or session.revoked_at is not None
        or ensure_utc(session.expires_at) <= utcnow()
        or str(session.user_id) != payload["sub"]
    ):
        raise unauthenticated
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise unauthenticated
    return AuthContext(user=user, session=session)


AuthDep = Annotated[AuthContext, Depends(get_auth_context)]


def require_role(*roles: UserRole) -> Callable[[AuthContext], AuthContext]:
    def checker(ctx: AuthDep) -> AuthContext:
        if ctx.user.role not in roles:
            raise PermissionDeniedError("You do not have permission for this action.")
        return ctx

    return checker


AdminDep = Annotated[AuthContext, Depends(require_role(UserRole.ADMIN))]
