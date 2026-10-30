"""Broker connection use-cases: connect (token paste), disconnect, sync, health, permissions.

Secrets: the access token arrives once in the connect request body, is validated with a
read-only broker call, encrypted (app.brokers.crypto) and stored only as ciphertext. It is
never logged, audited, returned or sent to the app again. Disconnect deletes it.

Health used by the execution path is local (connection status + token expiry); only the
explicit health endpoint and sync call the broker.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers import crypto
from app.brokers import registry as REG
from app.brokers.base import BrokerError, BrokerUnavailable
from app.brokers.client import (
    BrokerAuthError,
    BrokerClient,
    VerificationStatus,
    to_jsonable,
)
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ServiceUnavailableError
from app.core.time import ensure_utc, utcnow
from app.models.accounts import Account
from app.models.audit import AuditLog
from app.models.bots import RiskSettings
from app.models.brokers import BrokerConnection, BrokerSnapshot

MIN_TOKEN_LEN, MAX_TOKEN_LEN = 16, 4096


# ------------------------------------------------------------------ lookups


def get_connection(db: Session, user_id: uuid.UUID, broker: str) -> BrokerConnection | None:
    return db.scalar(select(BrokerConnection).where(BrokerConnection.user_id == user_id,
                                                    BrokerConnection.broker == broker))


def connection_for_account(db: Session, account: Account) -> BrokerConnection | None:
    """The connection that serves a REAL account (CONNECTED first, then the newest)."""
    rows = db.scalars(select(BrokerConnection).where(
        BrokerConnection.account_id == account.id)).all()
    if not rows:
        return None
    return sorted(rows, key=lambda c: (c.status == "CONNECTED", ensure_utc(c.updated_at)))[-1]


def latest_snapshot(db: Session, account_id: uuid.UUID, kind: str) -> BrokerSnapshot | None:
    return db.scalar(select(BrokerSnapshot).where(
        BrokerSnapshot.account_id == account_id, BrokerSnapshot.kind == kind)
        .order_by(BrokerSnapshot.fetched_at.desc()).limit(1))


def token_expired(conn: BrokerConnection, now: datetime | None = None) -> bool:
    return conn.token_expires_at is not None and \
        ensure_utc(conn.token_expires_at) <= (now or utcnow())


def connection_health(conn: BrokerConnection | None,
                      now: datetime | None = None) -> dict[str, Any]:
    """Local health (no network): used by every REAL pre-trade check."""
    if conn is None:
        return {"ok": False, "status": "NOT_CONNECTED", "detail": "No broker is connected."}
    if conn.status != "CONNECTED":
        why = f" ({conn.last_error})" if conn.last_error else ""
        return {"ok": False, "status": conn.status,
                "detail": f"The broker connection is {conn.status}{why}."}
    if not conn.token_ciphertext:
        return {"ok": False, "status": "ERROR", "detail": "No stored broker token."}
    if token_expired(conn, now):
        return {"ok": False, "status": "EXPIRED", "detail": "The broker access token expired."}
    return {"ok": True, "status": "CONNECTED", "detail": "Broker connection is active."}


# ------------------------------------------------------------------ clients


class ConnectionBoundClient:
    """A broker client whose `health()` reports the stored connection state (no network).
    `live_health()` performs the broker's read-only check."""

    def __init__(self, inner: BrokerClient, conn: BrokerConnection) -> None:
        self._inner, self._conn = inner, conn
        self.name = inner.name

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    def health(self) -> dict[str, Any]:
        return connection_health(self._conn)

    def live_health(self) -> dict[str, Any]:
        return self._inner.health()


def client_for_connection(db: Session, settings: Settings,
                          conn: BrokerConnection) -> BrokerClient:
    if not conn.token_ciphertext:
        raise ConflictError("The broker is not connected.", code="BROKER_NOT_CONNECTED")
    try:
        token = crypto.decrypt(settings, conn.token_ciphertext)
    except (crypto.TokenDecryptError, crypto.EncryptionUnavailable) as exc:
        conn.status, conn.last_error = "ERROR", str(exc)[:300]
        raise ConflictError("The stored broker token cannot be used. Reconnect the broker.",
                            code="BROKER_TOKEN_UNREADABLE") from exc
    return ConnectionBoundClient(REG.build_client(settings, conn.broker, token), conn)  # type: ignore[return-value]


def real_broker_client(db: Session, settings: Settings, account: Account) -> BrokerClient:
    """The client the REAL execution adapter uses for this account."""
    from app.brokers.real import UnavailableBrokerClient

    conn = connection_for_account(db, account)
    h = connection_health(conn)
    if conn is None or not h["ok"]:
        return UnavailableBrokerClient(h["detail"])
    try:
        return client_for_connection(db, settings, conn)
    except AppError as exc:
        return UnavailableBrokerClient(exc.message)


# ------------------------------------------------------------------ connect


def _known(broker: str) -> REG.BrokerInfo:
    info = REG.get_info(broker)
    if info is None:
        raise NotFoundError(f"Unknown broker {broker!r}.", code="BROKER_NOT_FOUND")
    return info


def connect(db: Session, settings: Settings, user_id: uuid.UUID, broker: str, token: str,
            now: datetime | None = None) -> BrokerConnection:
    from app.services.accounts import link_real_account

    now = now or utcnow()
    info = _known(broker)
    if info.verification == VerificationStatus.NOT_INTEGRATED:
        raise ConflictError(f"{info.name} is not integrated in this build.",
                            code="BROKER_NOT_INTEGRATED")
    if not info.enabled(settings):
        raise ConflictError(
            f"The {info.name} integration is disabled on this server. It is unverified "
            "against the live API; an operator must enable it explicitly.",
            code="BROKER_NOT_ENABLED")
    if not crypto.available():
        raise ServiceUnavailableError(
            "Token encryption is not available on this server, so no token can be stored.",
            code="BROKER_ENCRYPTION_UNAVAILABLE")
    token = (token or "").strip()
    if not (MIN_TOKEN_LEN <= len(token) <= MAX_TOKEN_LEN) or any(c.isspace() for c in token):
        raise AppError("That does not look like an access token.", code="INVALID_BROKER_TOKEN",
                       status_code=422)
    other = db.scalar(select(BrokerConnection).where(
        BrokerConnection.user_id == user_id, BrokerConnection.broker != broker,
        BrokerConnection.status == "CONNECTED"))
    if other is not None:
        raise ConflictError("Another broker is already connected. Disconnect it first.",
                            code="BROKER_ALREADY_CONNECTED")
    client = REG.build_client(settings, broker, token)
    try:
        profile = client.profile()  # read-only validation
    except BrokerAuthError as exc:
        raise AppError("The broker refused this access token.", code="BROKER_TOKEN_REJECTED",
                       status_code=400) from exc
    except (BrokerUnavailable, BrokerError) as exc:
        raise ServiceUnavailableError("The broker could not be reached to validate the token.",
                                      code="BROKER_UNAVAILABLE") from exc
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
    account = link_real_account(db, user_id, broker)
    conn = get_connection(db, user_id, broker)
    if conn is None:
        conn = BrokerConnection(user_id=user_id, broker=broker, account_id=account.id,
                                account_mode="REAL", auth_flow=info.auth_flow.value,  # type: ignore[union-attr]
                                status="CONNECTED", created_at=now)
        db.add(conn)
    conn.account_id = account.id
    conn.status, conn.last_error, conn.revoked_at = "CONNECTED", None, None
    conn.token_ciphertext = crypto.encrypt(settings, token)
    # Upper bound: the broker does not tell us when the pasted token was generated.
    conn.token_expires_at = now + timedelta(hours=info.token_ttl_hours or 24)
    conn.scopes = list(profile.scopes)
    conn.last_checked_at, conn.updated_at = now, now
    db.flush()
    account.broker_connection_ref = str(conn.id)
    return conn


def disconnect(db: Session, settings: Settings, user_id: uuid.UUID, broker: str,
               now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    info = _known(broker)
    conn = get_connection(db, user_id, broker)
    if conn is None or conn.status == "REVOKED":
        raise NotFoundError("This broker is not connected.", code="BROKER_NOT_CONNECTED")
    conn.status, conn.token_ciphertext, conn.revoked_at = "REVOKED", None, now
    conn.updated_at, conn.last_error = now, None
    acc = db.get(Account, conn.account_id)
    if acc is not None:
        acc.status, acc.broker_connection_ref = "DISCONNECTED", None
    caps = info.capabilities
    db.flush()
    return {"broker": broker, "status": "REVOKED", "revoked_at": now,
            "broker_revoke": "SUPPORTED" if caps and caps.revoke_token else "NOT_SUPPORTED",
            "note": "The token was deleted from this server. The broker has no revoke API: "
                    "also revoke it on the broker's access-token page."
            if not (caps and caps.revoke_token) else "Revoked at the broker."}


def _usable(db: Session, user_id: uuid.UUID, broker: str,
            now: datetime) -> BrokerConnection:
    _known(broker)
    conn = get_connection(db, user_id, broker)
    if conn is None or conn.status == "REVOKED" or not conn.token_ciphertext:
        raise ConflictError("This broker is not connected.", code="BROKER_NOT_CONNECTED")
    if token_expired(conn, now):
        conn.status = "EXPIRED"
        raise ConflictError("The broker access token expired. Paste a new one.",
                            code="BROKER_TOKEN_EXPIRED")
    return conn


def _fail(conn: BrokerConnection, exc: Exception, now: datetime) -> AppError:
    conn.last_checked_at, conn.updated_at = now, now
    if isinstance(exc, BrokerAuthError):
        conn.status, conn.last_error = "EXPIRED", "The broker refused the access token."
        return ConflictError("The broker refused the access token. Paste a new one.",
                             code="BROKER_TOKEN_EXPIRED")
    conn.status, conn.last_error = "ERROR", str(exc)[:300]
    return ServiceUnavailableError("The broker could not be reached.", code="BROKER_UNAVAILABLE")


def sync(db: Session, settings: Settings, user_id: uuid.UUID, broker: str,
         now: datetime | None = None) -> dict[str, Any]:
    """Pull funds, positions, orders and fills into snapshots (all or nothing)."""
    from app.brokers.reconcile import reconcile_charges
    from app.execution.risk import trading_day

    now = now or utcnow()
    conn = _usable(db, user_id, broker, now)
    client = client_for_connection(db, settings, conn)
    try:
        funds = client.funds()
        positions = client.positions()
        orders = client.orders()
        trades = client.tradebook()
    except (BrokerAuthError, BrokerUnavailable, BrokerError) as exc:
        raise _fail(conn, exc, now) from exc
    payloads = {"FUNDS": to_jsonable(funds), "POSITIONS": to_jsonable(positions),
                "ORDERS": to_jsonable(orders), "FILLS": to_jsonable(trades)}
    for kind, payload in payloads.items():
        db.add(BrokerSnapshot(connection_id=conn.id, account_id=conn.account_id,
                              account_mode="REAL", broker=broker, kind=kind, payload=payload,
                              fetched_at=now))
    conn.status, conn.last_error = "CONNECTED", None
    conn.last_sync_at = conn.last_checked_at = conn.updated_at = now
    acc = db.get(Account, conn.account_id)
    recon = reconcile_charges(db, acc, trading_day(now), funds, broker) if acc else None
    db.flush()
    return {"broker": broker, "fetched_at": now, "counts": {
        "positions": len(positions), "orders": len(orders), "fills": len(trades)},
        "charges_reconciliation": None if recon is None else {
            "trading_day": recon.trading_day.isoformat(),
            "broker_reported": str(recon.broker_reported), "estimated": str(recon.estimated),
            "diff": str(recon.diff), "fills": recon.fills}}


def health_check(db: Session, settings: Settings, user_id: uuid.UUID, broker: str,
                 now: datetime | None = None) -> dict[str, Any]:
    """Explicit health check; calls the broker's read-only endpoint when connected."""
    now = now or utcnow()
    _known(broker)
    conn = get_connection(db, user_id, broker)
    out: dict[str, Any] = {"broker": broker, "checked_at": now, "live_check": False}
    if conn is not None and conn.status == "CONNECTED" and token_expired(conn, now):
        conn.status, conn.updated_at = "EXPIRED", now
    local = connection_health(conn, now)
    if conn is None or not local["ok"]:
        out.update(local)
    else:
        try:
            client = client_for_connection(db, settings, conn)
            live = client.live_health()  # type: ignore[attr-defined]
        except AppError as exc:
            live = {"ok": False, "status": "ERROR", "detail": exc.message}
        conn.last_checked_at, conn.updated_at = now, now
        if not live["ok"]:
            conn.status, conn.last_error = live.get("status", "ERROR"), str(live["detail"])[:300]
        out.update(live, live_check=True)
    out.update(last_sync_at=conn.last_sync_at if conn else None,
               token_expires_at=conn.token_expires_at if conn else None)
    db.flush()
    return out


# ------------------------------------------------------------------ permissions


def permissions(db: Session, settings: Settings, user_id: uuid.UUID,
                now: datetime | None = None) -> dict[str, Any]:
    """Checklist for activating live trading. In this build it can never be all-green."""
    from app.brokers.real import active_authorization

    now = now or utcnow()
    conns = db.scalars(select(BrokerConnection).where(BrokerConnection.user_id == user_id)).all()
    conn = next((c for c in conns if c.status == "CONNECTED"), None) or \
        (max(conns, key=lambda c: ensure_utc(c.updated_at)) if conns else None)
    info = REG.get_info(conn.broker) if conn else None
    acc = db.get(Account, conn.account_id) if conn else None
    h = connection_health(conn, now)
    limits_saved = db.scalar(select(AuditLog.id).where(
        AuditLog.user_id == user_id, AuditLog.action == "risk.limits_updated",
        AuditLog.outcome == "SUCCESS").limit(1)) is not None
    has_limits = db.get(RiskSettings, user_id) is not None
    checks = [
        ("broker_connected", "Broker connected",
         conn is not None and conn.status == "CONNECTED",
         "No broker is connected." if conn is None
         else f"The connection is {conn.status}."),
        ("token_valid", "Access token valid", h["ok"], h["detail"]),
        ("order_permission", "Token permissions include order placement",
         conn is not None and "orders.write" in (conn.scopes or []),
         "Order permission cannot be verified without placing an order, and INDstocks "
         "also requires a whitelisted static IP for orders."),
        ("integration_verified", "Broker integration verified against the live API",
         info is not None and info.verification == VerificationStatus.VERIFIED,
         "The broker adapter has only been tested against a documented fake "
         f"({(info.verification.value if info else 'NOT_INTEGRATED')})."),
        ("live_flag", "Live trading enabled on the server", settings.live_trading_enabled,
         "LIVE_TRADING_ENABLED is false and cannot be turned on in this build."),
        ("live_authorization", "Your explicit live-trading authorisation",
         acc is not None and active_authorization(db, acc) is not None,
         "No live-trading authorisation has been granted (it cannot be in this build)."),
        ("risk_limits", "Risk limits reviewed and saved", has_limits and limits_saved,
         "Default risk limits are in use; review and save them in Risk Management."),
        ("regulatory", "Regulatory and broker review",
         False, "Not done: algo-order approval (exchange/broker), static-IP registration and "
                "a compliance review are outside this app."),
    ]
    items = [{"key": k, "label": label, "passed": bool(ok), "detail": None if ok else why}
             for k, label, ok, why in checks]
    return {"ready": all(i["passed"] for i in items), "live_trading_enabled": False,
            "broker": conn.broker if conn else None, "checks": items,
            "blockers": [i["detail"] for i in items if not i["passed"]]}
