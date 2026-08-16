"""
Alembic migration environment.

Migrations run synchronously on psycopg2 while the application runs on
asyncpg. An async migration environment would add machinery for no benefit:
migrations are a short-lived, single-connection, strictly serial operation.

The URL always comes from DATABASE_URL, never from alembic.ini, so the same
command works locally and on Railway without an ini file per environment.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `src` importable when alembic is invoked from the core-engine root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.base import Base, database_url, sync_url  # noqa: E402
from src.db import models  # noqa: E402,F401  (imported for side effect: table registration)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", sync_url(database_url()))

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    """
    Keep autogenerate focused on tables this project owns.

    Without this, a shared database (or an extension that creates its own
    tables) produces migrations that try to drop tables Alembic did not
    create.
    """
    if type_ == "table" and name in {"alembic_version"}:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
