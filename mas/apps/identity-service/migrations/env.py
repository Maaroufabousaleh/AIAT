"""Alembic environment for the private identity Postgres database."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

dsn = os.getenv("IDENTITY_DATABASE_DSN")
if not dsn and os.getenv("IDENTITY_DATABASE_PASSWORD"):
    dsn = URL.create(
        "postgresql+psycopg2",
        username=os.getenv("IDENTITY_DATABASE_USER", "identity_service"),
        password=os.environ["IDENTITY_DATABASE_PASSWORD"],
        host=os.getenv("IDENTITY_DATABASE_HOST", "identity-postgres"),
        port=int(os.getenv("IDENTITY_DATABASE_PORT", "5432")),
        database=os.getenv("IDENTITY_DATABASE_NAME", "identity"),
    ).render_as_string(hide_password=False)
if dsn:
    # Alembic's synchronous engine uses the bundled psycopg2 driver, not
    # asyncpg used by the service runtime.
    config.set_main_option("sqlalchemy.url", dsn.replace("+asyncpg", ""))


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
