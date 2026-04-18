"""Alembic migration environment — async SQLAlchemy with asyncpg.

Usage
─────
  # Apply all pending migrations
  alembic upgrade head

  # Roll back one migration
  alembic downgrade -1

  # Auto-generate a new migration from model changes
  alembic revision --autogenerate -m "your description"

Run these commands from the backend/ directory so that the app.* package is
on sys.path (alembic.ini sets prepend_sys_path = .).
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import ORM metadata so autogenerate can diff against the live DB schema
from app.models.orm import Base          # noqa: E402  (must be after sys.path setup)
from app.core.config import settings     # noqa: E402

# ── Alembic Config object (wraps alembic.ini) ─────────────────────────────────
config = context.config

# Override the sqlalchemy.url from application settings so we never have to
# keep it in sync across two places.
config.set_main_option("sqlalchemy.url", settings.database_url)

# Configure Python logging from the ini file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used by --autogenerate
target_metadata = Base.metadata


# ── Offline mode (generates SQL without a live DB connection) ─────────────────

def run_migrations_offline() -> None:
    """Emit SQL to stdout; useful for review or applying via a DBA."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Render CREATE TABLE with IF NOT EXISTS for safety
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (connects and applies migrations) ─────────────────────────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # compare_type catches column-type changes
        compare_type=True,
        # compare_server_default catches DEFAULT value changes
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine, connect, and run migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # no pool for migration runs
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
