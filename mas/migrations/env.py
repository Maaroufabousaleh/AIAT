"""
Alembic environment configuration.

Reads PGBOUNCER_DSN (or DATABASE_URL) from environment variables so that
the same migration scripts work in all environments (local, CI, production)
without hard-coding credentials.

Running migrations
------------------
# From the mas/ workspace root:
#   uv run alembic upgrade head
#   uv run alembic downgrade -1
#   uv run alembic revision --autogenerate -m "your message"
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --------------------------------------------------------------------------- #
# Alembic Config                                                               #
# --------------------------------------------------------------------------- #
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --------------------------------------------------------------------------- #
# Target metadata                                                               #
# SQLAlchemy MetaData with all table definitions from Phase 7.                #
# --------------------------------------------------------------------------- #
from mas_core.memory.models import metadata as target_metadata

# --------------------------------------------------------------------------- #
# Connection URL                                                                #
# --------------------------------------------------------------------------- #
_url = (
    os.environ.get("PGBOUNCER_DSN")
    or os.environ.get("DATABASE_URL")
    or "postgresql://mas_user:change_me@localhost:5432/mas"
)

# PgBouncer transaction-mode: statement_cache_size MUST be 0.
_connect_args: dict = {"statement_cache_size": 0}


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (useful for CI review)."""
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live DB (default mode)."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _url

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_connect_args,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
