"""Alembic migration environment."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from urllib.parse import urlparse, urlunparse

from api.config import get_api_settings
from api.database.connection import Base
from api.database import models  # noqa: F401 - register models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Return the database URL, honoring -x dbname or -x url command-line arguments."""
    x_args = context.get_x_argument(as_dictionary=True)
    if "url" in x_args:
        return x_args["url"]
    settings = get_api_settings()
    url = settings.migration_database_url or settings.database_url
    if "dbname" in x_args:
        parsed = urlparse(url)
        url = urlunparse(parsed._replace(path=f"/{x_args['dbname']}"))
    return url


def get_sync_url() -> str:
    """Synchronous database URL for offline mode or sync drivers."""
    return get_url().replace("+asyncpg", "").replace("+aiosqlite", "")


def include_object(object, name, type_, reflected, compare_to):
    """Ensure Alembic only operates on tables in the gid schema."""
    if type_ == "table":
        return object.schema == "gid"
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="gid",
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema="gid",
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode (async)."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
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
