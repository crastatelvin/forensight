"""Alembic environment. Uses the app's async DB URL and Base metadata.

Runs migrations synchronously via a sync driver derived from the async URL so
`alembic upgrade head` / `downgrade base` work in CI without an event loop
(test T0.3).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

# Import models so their tables register on Base.metadata for autogenerate.
from app.auth import models as _auth_models  # noqa: F401
from app.config import get_settings
from app.db import Base
from app.domain import models as _domain_models  # noqa: F401
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    # Alembic runs migrations with a synchronous driver.
    return get_settings().database_url.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
