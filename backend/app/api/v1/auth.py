from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import AuthDep, DbDep, OtpSenderDep, SettingsDep
from app.core.rate_limit import rate_limit
from app.models.auth import VerificationPurpose
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenPair,
    VerifyRequest,
)
from app.schemas.common import Message
from app.schemas.user import UserOut
from app.services import auth as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])

_GENERIC_CODE_MSG = "If the account exists, a verification code has been sent."


def _tokens(issued: svc.IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        user=UserOut.model_validate(issued.user),
    )


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth:register"))],
)
def register(
    body: RegisterRequest, request: Request, db: DbDep, settings: SettingsDep,
    sender: OtpSenderDep,
) -> RegisterResponse:
    user, channel = svc.register_user(db, settings, sender, body, request)
    return RegisterResponse(user=UserOut.model_validate(user), verification_channel=channel.value)


@router.post("/verify", response_model=TokenPair,
             dependencies=[Depends(rate_limit("auth:verify"))])
def verify(body: VerifyRequest, request: Request, db: DbDep, settings: SettingsDep) -> TokenPair:
    return _tokens(
        svc.verify_account(db, settings, identifier=body.identifier, code=body.code,
                           request=request, device_name=body.device_name)
    )


@router.post("/verify/resend", response_model=Message, status_code=202,
             dependencies=[Depends(rate_limit("auth:resend"))])
def resend_verification(
    body: ResendVerificationRequest, request: Request, db: DbDep, settings: SettingsDep,
    sender: OtpSenderDep,
) -> Message:
    user = svc.find_user_by_identifier(db, body.identifier)
    if user is not None and user.is_active and not user.is_verified:
        svc.issue_code(db, settings, sender, user=user,
                       purpose=VerificationPurpose.VERIFY_ACCOUNT, identifier=body.identifier)
        record_audit(db, action="auth.verify_resend", user_id=user.id, request=request)
        db.commit()
    return Message(message=_GENERIC_CODE_MSG)


@router.post("/login", response_model=TokenPair,
             dependencies=[Depends(rate_limit("auth:login"))])
def login(body: LoginRequest, request: Request, db: DbDep, settings: SettingsDep) -> TokenPair:
    user = svc.authenticate(db, settings, identifier=body.identifier, password=body.password,
                            request=request)
    record_audit(db, action="auth.login", user_id=user.id, request=request)
    return _tokens(svc.create_session(db, settings, user=user, request=request,
                                      device_name=body.device_name))


@router.post("/refresh", response_model=TokenPair,
             dependencies=[Depends(rate_limit("auth:refresh", limit=30))])
def refresh(body: RefreshRequest, request: Request, db: DbDep, settings: SettingsDep) -> TokenPair:
    return _tokens(svc.rotate_refresh(db, settings, token=body.refresh_token, request=request))


@router.post("/logout", status_code=204)
def logout(ctx: AuthDep, request: Request, db: DbDep) -> None:
    svc.revoke_session(db, ctx.session, "logout")
    record_audit(db, action="auth.logout", user_id=ctx.user.id, request=request,
                 entity_type="session", entity_id=str(ctx.session.id))
    db.commit()


@router.post("/password/forgot", response_model=Message, status_code=202,
             dependencies=[Depends(rate_limit("auth:forgot"))])
def forgot_password(
    body: ForgotPasswordRequest, request: Request, db: DbDep, settings: SettingsDep,
    sender: OtpSenderDep,
) -> Message:
    svc.start_password_reset(db, settings, sender, identifier=body.identifier, request=request)
    return Message(message=_GENERIC_CODE_MSG)


@router.post("/password/reset", response_model=Message,
             dependencies=[Depends(rate_limit("auth:reset"))])
def reset_password(
    body: ResetPasswordRequest, request: Request, db: DbDep, settings: SettingsDep
) -> Message:
    svc.complete_password_reset(db, settings, identifier=body.identifier, code=body.code,
                                new_password=body.new_password, request=request)
    return Message(message="Password updated. Please sign in again on all devices.")
