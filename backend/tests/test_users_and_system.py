from __future__ import annotations

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import User, UserRole
from tests.conftest import PASSWORD, auth_header, register_and_verify


def test_preferences_roundtrip_and_validation(client, otp):
    h = auth_header(register_and_verify(client, otp))
    r = client.get("/api/v1/users/me/preferences", headers=h)
    assert r.status_code == 200
    assert r.json()["theme"] == "dark"
    assert r.json()["timezone"] == "Asia/Kolkata"

    r = client.patch("/api/v1/users/me/preferences", headers=h,
                     json={"language": "hinglish", "notifications": {"daily_report": False}})
    assert r.status_code == 200
    assert r.json()["language"] == "hinglish"
    assert r.json()["notifications"]["daily_report"] is False
    assert r.json()["notifications"]["risk_alerts"] is True

    r = client.patch("/api/v1/users/me/preferences", headers=h,
                     json={"notifications": {"bogus": True}})
    assert r.status_code == 422
    r = client.patch("/api/v1/users/me/preferences", headers=h, json={"theme": "neon"})
    assert r.status_code == 422


def test_session_listing_and_remote_revocation(client, otp):
    t1 = register_and_verify(client, otp)
    t2 = client.post("/api/v1/auth/login", json={
        "identifier": "trader@example.com", "password": PASSWORD,
        "device_name": "Pixel"}).json()
    sessions = client.get("/api/v1/users/me/sessions", headers=auth_header(t1)).json()
    assert len(sessions) == 2
    assert sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if s["device_name"] == "Pixel")
    r = client.delete(f"/api/v1/users/me/sessions/{other['id']}", headers=auth_header(t1))
    assert r.status_code == 204
    assert client.get("/api/v1/users/me", headers=auth_header(t2)).status_code == 401
    assert client.get("/api/v1/users/me", headers=auth_header(t1)).status_code == 200


def test_cannot_revoke_another_users_session(client, otp):
    a = register_and_verify(client, otp, "a@example.com")
    b = register_and_verify(client, otp, "b@example.com")
    b_sid = client.get("/api/v1/users/me/sessions", headers=auth_header(b)).json()[0]["id"]
    r = client.delete(f"/api/v1/users/me/sessions/{b_sid}", headers=auth_header(a))
    assert r.status_code == 404


def test_change_password_signs_out_other_devices(client, otp):
    t1 = register_and_verify(client, otp)
    t2 = client.post("/api/v1/auth/login", json={
        "identifier": "trader@example.com", "password": PASSWORD}).json()
    r = client.post("/api/v1/users/me/password", headers=auth_header(t1), json={
        "current_password": PASSWORD, "new_password": "Brand-new-pass-99"})
    assert r.status_code == 200
    assert client.get("/api/v1/users/me", headers=auth_header(t1)).status_code == 200
    assert client.get("/api/v1/users/me", headers=auth_header(t2)).status_code == 401


def test_own_audit_history_is_paginated(client, otp):
    h = auth_header(register_and_verify(client, otp))
    r = client.get("/api/v1/users/me/audit-logs?limit=2", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 2 and len(body["items"]) == 2 and body["total"] >= 3
    assert client.get("/api/v1/users/me/audit-logs?limit=500", headers=h).status_code == 422


def test_admin_endpoint_requires_admin_role(client, otp, engine):
    tokens = register_and_verify(client, otp)
    h = auth_header(tokens)
    r = client.get("/api/v1/admin/audit-logs", headers=h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PERMISSION_DENIED"

    with Session(engine) as db:
        db.execute(update(User).values(role=UserRole.ADMIN))
        db.commit()
    r = client.get("/api/v1/admin/audit-logs?action=auth.register", headers=h)
    assert r.status_code == 200
    assert all(i["action"] == "auth.register" for i in r.json()["items"])


def test_health_and_request_id(client):
    r = client.get("/api/v1/health/live")
    assert r.status_code == 200
    r = client.get("/api/v1/health/ready", headers={"X-Request-ID": "abc12345-req"})
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "abc12345-req"
    comps = {c["name"]: c["status"] for c in r.json()["components"]}
    assert comps["database"] == "ok"
    assert comps["broker"] == "not_configured"


def test_error_envelope_shape(client):
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    err = r.json()["error"]
    assert set(err) == {"code", "message", "details", "request_id"}
    assert err["request_id"]


def test_system_status_reports_live_trading_locked(client):
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["live_trading_enabled"] is False
    assert body["paper_trading_enabled"] is True
    assert body["is_demo_data"] is True
    assert body["live_trading_blockers"]


def test_demo_market_data_is_labelled_and_requires_auth(client, otp):
    assert client.get("/api/v1/market/indices").status_code == 401
    h = auth_header(register_and_verify(client, otp))
    body = client.get("/api/v1/market/indices", headers=h).json()
    assert body["source"] == "DEMO" and body["is_demo"] is True and body["is_live"] is False
    assert body["disclaimer"]
    assert {i["symbol"] for i in body["items"]} >= {"NIFTY", "BANKNIFTY"}
    assert all("(demo)" in i["name"] for i in body["items"])


def test_settings_refuse_live_trading_and_insecure_production():
    with pytest.raises(ValueError, match="LIVE_TRADING_ENABLED"):
        Settings(live_trading_enabled=True)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="production", database_url="postgresql+psycopg://x/y",
                 otp_delivery="none",
                 secret_key="dev-only-insecure-secret-change-me-0000000000000000")
    with pytest.raises(ValueError, match="SQLite"):
        Settings(environment="production", secret_key="x" * 40, otp_delivery="none")
    ok = Settings(environment="production", secret_key="x" * 40, otp_delivery="none",
                  database_url="postgresql+psycopg://u@h/db")
    assert ok.live_trading_enabled is False
