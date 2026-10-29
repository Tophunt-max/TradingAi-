from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine(url: str, echo: bool = False) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, echo=echo, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(url, echo=echo, pool_pre_ping=True, pool_size=10, max_overflow=20)


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        s = get_settings()
        _engine = _make_engine(s.database_url, s.database_echo)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def configure_engine(engine: Engine) -> None:
    """Swap the engine (used by tests)."""
    global _engine, _SessionLocal
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
