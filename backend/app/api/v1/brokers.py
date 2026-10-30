"""Broker connections: supported brokers, connect (token paste), disconnect, sync, health and
the live-trading permission checklist. See docs/broker-integration.md.

The broker access token is accepted only in the connect request body. It is never logged,
audited or returned. Live trading stays locked whatever the connection state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, SecretStr

from app.api.deps import AuthDep, DbDep, SettingsDep
from app.brokers import connections as svc
from app.brokers import registry as REG
from app.brokers.client import VerificationStatus
from app.core.errors import AppError
from app.services.audit import record_audit

router = APIRouter(prefix="/brokers", tags=["brokers"])

Status = Literal["CONNECTED", "NOT_INTEGRATED", "NOT_CONNECTED", "NOT_ENABLED", "EXPIRED",
                 "REVOKED", "ERROR"]


class ConnectionOut(BaseModel):
    status: Literal["CONNECTED", "EXPIRED", "REVOKED", "ERROR"]
    account_id: str
    auth_flow: str
    scopes: list[str]
    token_expires_at: datetime | None
    last_sync_at: datetime | None
    last_checked_at: datetime | None
    last_error: str | None
    connected_at: datetime
    revoked_at: datetime | None


class BrokerOut(BaseModel):
    id: str
    name: str
    kind: Literal["PAPER", "BROKER"]
    status: Status
    supports_live_orders: bool
    note: str
    last_sync: datetime | None
    # Additive fields (v2): never claim more than the integration implements.
    verification: str
    enabled: bool
    auth_flow: str | None = None
    capabilities: dict[str, Any] | None = None
    docs_url: str | None = None
    sources: list[str] = []
    connection: ConnectionOut | None = None


def _conn_out(c: Any) -> ConnectionOut:
    return ConnectionOut(status=c.status, account_id=str(c.account_id), auth_flow=c.auth_flow,
                         scopes=list(c.scopes or []), token_expires_at=c.token_expires_at,
                         last_sync_at=c.last_sync_at, last_checked_at=c.last_checked_at,
                         last_error=c.last_error, connected_at=c.created_at,
                         revoked_at=c.revoked_at)


@router.get("", response_model=list[BrokerOut])
def brokers(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> list[BrokerOut]:
    out = [BrokerOut(id="paper", name="Paper Trading", kind="PAPER", status="CONNECTED",
                     supports_live_orders=False,
                     note="Simulated account (default): bid/ask fills, slippage and charges.",
                     last_sync=None, verification="SIMULATOR", enabled=True)]
    for info in REG.BROKERS.values():
        if info.verification == VerificationStatus.NOT_INTEGRATED:
            out.append(BrokerOut(
                id=info.id, name=info.name, kind="BROKER", status="NOT_INTEGRATED",
                supports_live_orders=False, last_sync=None, verification=info.verification.value,
                enabled=False, note="Not integrated. Only official broker APIs will be used."))
            continue
        conn = svc.get_connection(db, ctx.user.id, info.id)
        enabled = info.enabled(settings)
        status: str = conn.status if conn else ("NOT_CONNECTED" if enabled else "NOT_ENABLED")
        note = ("Read-only connection: funds, positions, orders and fills sync. Order placement "
                "is locked. Adapter built from the official docs; not yet verified against "
                "the live API.")
        if not enabled:
            note += " Disabled on this server (BROKER_INDSTOCKS_ENABLED)."
        out.append(BrokerOut(
            id=info.id, name=info.name, kind="BROKER", status=status,  # type: ignore[arg-type]
            supports_live_orders=False, note=note,
            last_sync=conn.last_sync_at if conn else None,
            verification=info.verification.value, enabled=enabled,
            auth_flow=info.auth_flow.value if info.auth_flow else None,
            capabilities=info.capabilities.as_dict() if info.capabilities else None,
            docs_url=info.docs_url, sources=list(info.sources),
            connection=_conn_out(conn) if conn else None))
    return out


class PermissionCheck(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str | None


class PermissionsOut(BaseModel):
    ready: bool
    live_trading_enabled: bool
    broker: str | None
    checks: list[PermissionCheck]
    blockers: list[str]


@router.get("/permissions", response_model=PermissionsOut)
def permissions(ctx: AuthDep, db: DbDep, settings: SettingsDep) -> PermissionsOut:
    out = svc.permissions(db, settings, ctx.user.id)
    db.commit()
    return PermissionsOut(**out)


def _audited[T](db: DbDep, request: Request, ctx: AuthDep, action: str, broker: str,
                fn: Callable[[], T], details: Callable[[T], dict[str, Any]]) -> T:
    """Run a broker action; audit success or rejection. State changes made before a
    failure (e.g. marking a connection EXPIRED) are committed with the rejection."""
    try:
        result = fn()
    except AppError as exc:
        record_audit(db, action=action, user_id=ctx.user.id, request=request,
                     entity_type="broker", entity_id=broker, outcome="REJECTED",
                     details={"broker": broker, "code": exc.code})
        db.commit()
        raise
    record_audit(db, action=action, user_id=ctx.user.id, request=request, entity_type="broker",
                 entity_id=broker, details={"broker": broker, **details(result)})
    db.commit()
    return result


class ConnectIn(BaseModel):
    # SecretStr: never rendered in reprs, logs or validation errors.
    token: SecretStr = Field(description="Access token generated at the broker. Stored "
                                         "encrypted on the server; never returned.")


@router.post("/{broker}/connect", response_model=BrokerOut)
def connect(broker: str, body: ConnectIn, request: Request, ctx: AuthDep, db: DbDep,
            settings: SettingsDep) -> BrokerOut:
    conn = _audited(db, request, ctx, "broker.connect", broker,
                    lambda: svc.connect(db, settings, ctx.user.id, broker,
                                        body.token.get_secret_value()),
                    lambda c: {"account_id": str(c.account_id), "scopes": list(c.scopes),
                               "expires_at": c.token_expires_at.isoformat()
                               if c.token_expires_at else None})
    return next(b for b in brokers(ctx, db, settings) if b.id == conn.broker)


class DisconnectIn(BaseModel):
    confirm: bool = False


@router.post("/{broker}/disconnect")
def disconnect(broker: str, body: DisconnectIn, request: Request, ctx: AuthDep, db: DbDep,
               settings: SettingsDep) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        if not body.confirm:
            raise AppError("Disconnecting needs confirm: true.", code="CONFIRMATION_REQUIRED")
        return svc.disconnect(db, settings, ctx.user.id, broker)

    return _audited(db, request, ctx, "broker.disconnect", broker, run,
                    lambda r: {"broker_revoke": r["broker_revoke"]})


@router.post("/{broker}/sync")
def sync(broker: str, request: Request, ctx: AuthDep, db: DbDep,
         settings: SettingsDep) -> dict[str, Any]:
    return _audited(db, request, ctx, "broker.sync", broker,
                    lambda: svc.sync(db, settings, ctx.user.id, broker),
                    lambda r: {"counts": r["counts"]})


@router.get("/{broker}/health")
def health(broker: str, ctx: AuthDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    out = svc.health_check(db, settings, ctx.user.id, broker)
    db.commit()
    return out
