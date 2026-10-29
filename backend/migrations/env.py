from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (register models)
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return str(config.get_main_option("sqlalchemy.url")).startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=_is_sqlite(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _pragma(connection, sql: str) -> None:  # type: ignore[no-untyped-def]
    outer = connection.in_transaction()
    connection.exec_driver_sql(sql)
    if not outer:
        connection.commit()  # do not leave an autobegun transaction for alembic to inherit


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        # SQLite batch migrations rebuild a table (copy, DROP, RENAME). With foreign keys
        # enabled, the DROP would fire ON DELETE CASCADE on child tables and silently delete
        # their rows. Foreign keys are switched off for the migration (this only takes
        # effect outside a transaction, i.e. before any DML on this connection) and the
        # result is verified with foreign_key_check below.
        _pragma(connection, "PRAGMA foreign_keys=OFF")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()
        if sqlite:
            bad = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"Foreign key violations after migration: {bad[:5]}")
    if sqlite:
        # No-op while an outer transaction is open; callers that pass in their own
        # connection should dispose of it (tests do) so new connections get FKs back on.
        _pragma(connection, "PRAGMA foreign_keys=ON")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
