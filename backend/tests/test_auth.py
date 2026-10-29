from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import AuditLog, VerificationCode
from tests.conftest import PASSWORD, auth_header, register_and_verify


def test_register_requires_risk_disclosure(client):
    r = client.post("/api/v1/auth/register", json={
        "full_name": "A B", "email": "a@example.com", "password": PASSWORD,
        "accept_risk_disclosure": False,
    })
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_register_rejects_weak_password_and_missing_identifier(client):
    r = client.post("/api/v1/auth/register", json={
        "full_name": "A B", "email": "a@example.com", "password": "short",
        "accept_risk_disclosure": True,
    })
    assert r.status_code == 422
    r = client.post("/api/v1/auth/register", json={
        "full_name": "A B", "password": PASSWORD, "accept_risk_disclosure": True,
    })
    assert r.status_code == 422


def test_register_verify_login_flow(client, otp):
    tokens = register_and_verify(client, otp)
    assert tokens["token_type"] == "bearer"
    assert tokens["user"]["is_verified"] is True

    r = client.post("/api/v1/auth/login",
                    json={"identifier": "TRADER@example.com", "password": PASSWORD})
    assert r.status_code == 200
    me = client.get("/api/v1/users/me", headers=auth_header(r.json()))
    assert me.status_code == 200
    assert me.json()["email"] == "trader@example.com"


def test_phone_registration_normalises_indian_mobile(client, otp):
    r = client.post("/api/v1/auth/register", json={
        "full_name": "Phone User", "phone": "98765 43210", "password": PASSWORD,
        "accept_risk_disclosure": True,
    })
    assert r.status_code == 201
    assert r.json()["user"]["phone"] == "+919876543210"
    assert r.json()["verification_channel"] == "SMS"
    sent = otp.last_for("+919876543210")
    r = client.post("/api/v1/auth/verify", json={"identifier": "+919876543210",
                                                 "code": sent.code})
    assert r.status_code == 200


def test_duplicate_registration_conflicts(client, otp):
    register_and_verify(client, otp)
    r = client.post("/api/v1/auth/register", json={
        "full_name": "Dup", "email": "trader@example.com", "password": PASSWORD,
        "accept_risk_disclosure": True,
    })
    assert r.status_code == 409


def test_unverified_user_cannot_login(client, otp):
    client.post("/api/v1/auth/register", json={
        "full_name": "U V", "email": "u@example.com", "password": PASSWORD,
        "accept_risk_disclosure": True,
    })
    r = client.post("/api/v1/auth/login", json={"identifier": "u@example.com",
                                                "password": PASSWORD})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "VERIFICATION_REQUIRED"


def test_wrong_password_and_unknown_user_look_identical(client, otp):
    register_and_verify(client, otp)
    a = client.post("/api/v1/auth/login", json={"identifier": "trader@example.com",
                                                "password": "Wrong-pass-123"})
    b = client.post("/api/v1/auth/login", json={"identifier": "nobody@example.com",
                                                "password": "Wrong-pass-123"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_account_lockout_after_repeated_failures(client, otp):
    register_and_verify(client, otp)
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"identifier": "trader@example.com",
                                                "password": "Wrong-pass-123"})
    r = client.post("/api/v1/auth/login", json={"identifier": "trader@example.com",
                                                "password": PASSWORD})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"
    assert "Retry-After" in r.headers


def test_otp_wrong_code_attempts_exhaust_code(client, otp):
    client.post("/api/v1/auth/register", json={
        "full_name": "O T", "email": "o@example.com", "password": PASSWORD,
        "accept_risk_disclosure": True,
    })
    good = otp.last_for("o@example.com").code
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(5):
        r = client.post("/api/v1/auth/verify", json={"identifier": "o@example.com",
                                                     "code": wrong})
        assert r.status_code == 400
    r = client.post("/api/v1/auth/verify", json={"identifier": "o@example.com", "code": good})
    assert r.status_code == 400  # code burned after max attempts


def test_expired_otp_rejected(client, otp, engine):
    client.post("/api/v1/auth/register", json={
        "full_name": "E X", "email": "e@example.com", "password": PASSWORD,
        "accept_risk_disclosure": True,
    })
    with Session(engine) as db:
        db.execute(update(VerificationCode).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    code = otp.last_for("e@example.com").code
    r = client.post("/api/v1/auth/verify", json={"identifier": "e@example.com", "code": code})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_CODE"


def test_codes_are_not_stored_in_plaintext(client, otp, engine):
    client.post("/api/v1/auth/register", json={
        "full_name": "P T", "email": "p@example.com", "password": PASSWORD,
        "accept_risk_disclosure": True,
    })
    code = otp.last_for("p@example.com").code
    with Session(engine) as db:
        stored = db.scalars(select(VerificationCode.code_hash)).all()
    assert stored and all(code not in h for h in stored)


def test_refresh_rotation_and_reuse_detection(client, otp):
    tokens = register_and_verify(client, otp)
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r1.status_code == 200
    new = r1.json()
    assert new["refresh_token"] != tokens["refresh_token"]

    # Re-using the old token revokes the whole session.
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r2.status_code == 401
    r3 = client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r3.status_code == 401
    assert client.get("/api/v1/users/me", headers=auth_header(new)).status_code == 401


def test_logout_revokes_access_immediately(client, otp):
    tokens = register_and_verify(client, otp)
    h = auth_header(tokens)
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert client.get("/api/v1/users/me", headers=h).status_code == 401
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_forgot_password_is_enumeration_safe_and_reset_revokes_sessions(client, otp):
    tokens = register_and_verify(client, otp)
    unknown = client.post("/api/v1/auth/password/forgot",
                          json={"identifier": "ghost@example.com"})
    known = client.post("/api/v1/auth/password/forgot",
                        json={"identifier": "trader@example.com"})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()

    code = otp.last_for("trader@example.com").code
    new_pw = "An0ther-Strong-Pass"
    r = client.post("/api/v1/auth/password/reset", json={
        "identifier": "trader@example.com", "code": code, "new_password": new_pw})
    assert r.status_code == 200
    assert client.get("/api/v1/users/me", headers=auth_header(tokens)).status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "identifier": "trader@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "identifier": "trader@example.com", "password": new_pw}).status_code == 200


def test_invalid_and_tampered_tokens_rejected(client, otp):
    tokens = register_and_verify(client, otp)
    assert client.get("/api/v1/users/me").status_code == 401
    bad = tokens["access_token"][:-4] + "abcd"
    assert client.get("/api/v1/users/me",
                      headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_audit_log_records_auth_events_without_secrets(client, otp, engine):
    register_and_verify(client, otp)
    client.post("/api/v1/auth/login", json={"identifier": "trader@example.com",
                                            "password": "Wrong-pass-123"})
    with Session(engine) as db:
        rows = db.scalars(select(AuditLog)).all()
    actions = {r.action for r in rows}
    assert {"auth.register", "auth.verify", "auth.session_created", "auth.login"} <= actions
    assert any(r.outcome == "FAILURE" for r in rows)
    dump = repr([r.details for r in rows])
    assert PASSWORD not in dump and "Wrong-pass-123" not in dump


def test_auth_rate_limit(client):
    statuses = [
        client.post("/api/v1/auth/password/forgot",
                    json={"identifier": f"x{i}@example.com"}).status_code
        for i in range(12)
    ]
    assert statuses[:10] == [202] * 10
    assert statuses[10] == 429
