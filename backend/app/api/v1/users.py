from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.core.errors import AppError, NotFoundError
from app.core.rate_limit import rate_limit
from app.core.security import hash_password, verify_password
from app.models.audit import AuditLog
from app.models.auth import UserSession
from app.models.user import UserPreferences
from app.schemas.auth import ChangePasswordRequest, SessionOut
from app.schemas.common import Message, Page, PageParams
from app.schemas.user import AuditLogOut, PreferencesOut, PreferencesUpdate, UserOut, UserUpdate
from app.services.audit import record_audit
from app.services.auth import NOTIFICATION_KEYS, revoke_all_sessions, revoke_session

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("", response_model=UserOut)
def get_me(ctx: AuthDep) -> UserOut:
    return UserOut.model_validate(ctx.user)


@router.patch("", response_model=UserOut)
def update_me(body: UserUpdate, ctx: AuthDep, db: DbDep, request: Request) -> UserOut:
    if body.full_name is not None:
        ctx.user.full_name = " ".join(body.full_name.split())
    record_audit(db, action="user.profile_updated", user_id=ctx.user.id, request=request,
                 details=body.model_dump(exclude_none=True))
    db.commit()
    return UserOut.model_validate(ctx.user)


def _prefs(db: DbDep, ctx: AuthDep) -> UserPreferences:
    prefs = db.get(UserPreferences, ctx.user.id)
    if prefs is None:
        prefs = UserPreferences(user_id=ctx.user.id,
                                notifications={k: True for k in NOTIFICATION_KEYS})
        db.add(prefs)
        db.flush()
    return prefs


@router.get("/preferences", response_model=PreferencesOut)
def get_preferences(ctx: AuthDep, db: DbDep) -> PreferencesOut:
    prefs = _prefs(db, ctx)
    db.commit()
    return PreferencesOut.model_validate(prefs)


@router.patch("/preferences", response_model=PreferencesOut)
def update_preferences(body: PreferencesUpdate, ctx: AuthDep, db: DbDep) -> PreferencesOut:
    prefs = _prefs(db, ctx)
    data = body.model_dump(exclude_none=True)
    notifications = data.pop("notifications", None)
    if notifications is not None:
        unknown = set(notifications) - set(NOTIFICATION_KEYS)
        if unknown:
            raise AppError("Unknown notification keys.", code="VALIDATION_ERROR",
                           status_code=422, details=sorted(unknown))
        prefs.notifications = {**(prefs.notifications or {}), **notifications}
    for key, value in data.items():
        setattr(prefs, key, value)
    db.commit()
    return PreferencesOut.model_validate(prefs)


@router.post("/password", response_model=Message,
             dependencies=[Depends(rate_limit("users:password"))])
def change_password(
    body: ChangePasswordRequest, ctx: AuthDep, db: DbDep, request: Request
) -> Message:
    if not verify_password(body.current_password, ctx.user.password_hash):
        record_audit(db, action="user.password_change", user_id=ctx.user.id, request=request,
                     outcome="FAILURE")
        db.commit()
        raise AppError("Current password is incorrect.", code="INVALID_CREDENTIALS",
                       status_code=400)
    ctx.user.password_hash = hash_password(body.new_password)
    revoked = revoke_all_sessions(db, ctx.user.id, "password_changed", except_id=ctx.session.id)
    record_audit(db, action="user.password_change", user_id=ctx.user.id, request=request,
                 details={"other_sessions_revoked": revoked})
    db.commit()
    return Message(message="Password changed. Other devices have been signed out.")


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> list[SessionOut]:
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == ctx.user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_used_at.desc())
    ).all()
    out = []
    for s in rows:
        item = SessionOut.model_validate(s)
        item.current = s.id == ctx.session.id
        out.append(item)
    return out


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_my_session(session_id: uuid.UUID, ctx: AuthDep, db: DbDep, request: Request) -> None:
    session = db.get(UserSession, session_id)
    if session is None or session.user_id != ctx.user.id:
        raise NotFoundError("Session not found.")
    revoke_session(db, session, "user_revoked")
    record_audit(db, action="auth.session_revoked", user_id=ctx.user.id, request=request,
                 entity_type="session", entity_id=str(session_id))
    db.commit()


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def my_audit_logs(
    ctx: AuthDep, db: DbDep, page: Annotated[PageParams, Depends()]
) -> Page[AuditLogOut]:
    base = select(AuditLog).where(AuditLog.user_id == ctx.user.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(AuditLog.created_at.desc()).limit(page.limit).offset(page.offset)
    ).all()
    return Page(items=[AuditLogOut.model_validate(r) for r in rows], total=total,
                limit=page.limit, offset=page.offset)
