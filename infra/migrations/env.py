"""Alembic environment (build bible §11.8: "Alembic for schema").

The database URL comes from ``qa_copilot_repository.db`` (single source of
truth for URL resolution — same path the API uses) and the metadata from
``qa_copilot_repository.models.Base``.

Run from the repo root (``alembic.ini`` lives there):

    uv run alembic revision --autogenerate -m "initial core schema"
    uv run alembic upgrade head
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from qa_copilot_repository import db, models
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# The database URL is resolved at call time (env var DATABASE_URL, then the
# docker-compose default) — never baked into the ini file.
config.set_main_option("sqlalchemy.url", db.get_database_url())

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# "add your model's MetaData object here" for autogenerate support.
target_metadata = models.Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to script output)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to the database)."""
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
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
