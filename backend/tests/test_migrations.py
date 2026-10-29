from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import _make_engine
from tests.conftest import alembic_config


def test_upgrade_downgrade_upgrade(tmp_path: Path):
    eng = _make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    cfg = alembic_config(eng)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    tables = set(inspect(eng).get_table_names())
    assert {"users", "user_sessions", "refresh_tokens", "verification_codes",
            "audit_logs", "user_preferences"} <= tables


def test_models_match_migrations(engine):
    """Fails if a model changed without a matching Alembic migration."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
