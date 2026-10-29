from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-0123456789",
        "DATABASE_URL": "sqlite:///./unused-test.db",
        "REDIS_URL": "",
        "OTP_DELIVERY": "console",
        "MARKET_DATA_MODE": "DEMO",
    }
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import _make_engine, configure_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.otp_delivery import InMemoryOtpSender, get_otp_sender  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(engine: Engine) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """Fresh, migrated database per test.

    Defaults to SQLite. Set TEST_DATABASE_URL (e.g. a disposable PostgreSQL
    database) to run the same suite against PostgreSQL.
    """
    get_settings.cache_clear()
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    eng = _make_engine(url)
    cfg = alembic_config(eng)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    configure_engine(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def otp() -> InMemoryOtpSender:
    return InMemoryOtpSender()


@pytest.fixture
def client(engine: Engine, otp: InMemoryOtpSender) -> Iterator[TestClient]:
    rate_limit.set_store(rate_limit.MemoryStore())
    app = create_app()
    app.dependency_overrides[get_otp_sender] = lambda: otp
    with TestClient(app) as c:
        yield c
    rate_limit.set_store(None)


PASSWORD = "Str0ngPassw0rd!"


def register_and_verify(client: TestClient, otp: InMemoryOtpSender,
                        email: str = "trader@example.com") -> dict:
    r = client.post("/api/v1/auth/register", json={
        "full_name": "Test Trader", "email": email, "password": PASSWORD,
        "accept_risk_disclosure": True,
    })
    assert r.status_code == 201, r.text
    code = otp.last_for(email).code
    r = client.post("/api/v1/auth/verify", json={"identifier": email, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def auth_header(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
