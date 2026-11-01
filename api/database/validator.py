"""Alembic revision and schema lifecycle validation.

Ensures that FastAPI never starts against an unmigrated, outdated, or
incompatible PostgreSQL database schema, while preserving zero-credential
leakage in error messages.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.env_loader import _project_root

logger = logging.getLogger(__name__)


class DatabaseLifecycleError(Exception):
    """Base exception for database lifecycle errors."""


class DatabaseConnectionError(DatabaseLifecycleError):
    """Failed to connect to database for lifecycle validation."""


class DatabaseSchemaError(DatabaseLifecycleError):
    """Target PostgreSQL schema does not exist."""


class DatabaseRevisionError(DatabaseLifecycleError):
    """Alembic revision is missing, outdated, or mismatched."""


def get_expected_alembic_head(config_path: str | Path | None = None) -> str:
    """Inspect repository Alembic migration scripts and return expected head revision.

    Raises DatabaseLifecycleError if configuration or scripts are invalid or ambiguous.
    """
    if config_path is None:
        root = _project_root()
        config_path = root / "alembic.ini"
    else:
        config_path = Path(config_path)

    if not config_path.is_file():
        raise DatabaseLifecycleError(
            f"Alembic configuration file not found at: {config_path.name}"
        )

    try:
        alembic_cfg = Config(str(config_path))
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
    except Exception as exc:
        raise DatabaseLifecycleError(
            f"Failed to read Alembic migration configuration: {exc.__class__.__name__}"
        ) from None

    if not heads:
        raise DatabaseRevisionError(
            "No Alembic migration heads found in migration scripts."
        )

    if len(heads) > 1:
        raise DatabaseRevisionError(
            f"Ambiguous Alembic migration heads: multiple heads exist: {heads}. "
            "Merge migration heads before starting application."
        )

    return heads[0]


async def validate_database_revision(
    engine: AsyncEngine,
    schema: str = "gid",
    config_path: str | Path | None = None,
) -> str:
    """Validate that the PostgreSQL database has the expected Alembic revision.

    Returns the verified revision ID on success.
    Raises DatabaseLifecycleError on any missing, outdated, or mismatched state.
    Never exposes passwords or sensitive credentials.
    Does NOT invoke Alembic upgrade automatically.
    """
    expected_head = get_expected_alembic_head(config_path=config_path)

    try:
        async with engine.connect() as conn:
            # 1. Confirm schema existence
            stmt_schema = text(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema"
            )
            res_schema = await conn.execute(stmt_schema, {"schema": schema})
            if res_schema.scalar() is None:
                raise DatabaseSchemaError(
                    f"PostgreSQL schema '{schema}' does not exist in target database. "
                    "Run 'alembic upgrade head' before starting the application."
                )

            # 2. Confirm version table existence
            stmt_table = text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = 'alembic_version'"
            )
            res_table = await conn.execute(stmt_table, {"schema": schema})
            if res_table.scalar() is None:
                raise DatabaseRevisionError(
                    f"Alembic version table '{schema}.alembic_version' does not exist. "
                    "Run 'alembic upgrade head' before starting the application."
                )

            # 3. Query current revision
            stmt_version = text(f"SELECT version_num FROM {schema}.alembic_version")
            res_version = await conn.execute(stmt_version)
            rows = res_version.scalars().all()

            if not rows:
                raise DatabaseRevisionError(
                    f"Alembic version table '{schema}.alembic_version' contains no revision record. "
                    "Database has not been stamped. Run 'alembic upgrade head'."
                )

            if len(rows) > 1:
                raise DatabaseRevisionError(
                    f"Ambiguous revision state: multiple revisions found in "
                    f"'{schema}.alembic_version': {rows}. Resolve revision history."
                )

            current_version = rows[0]

            if current_version != expected_head:
                raise DatabaseRevisionError(
                    f"Database revision mismatch: database is at revision '{current_version}', "
                    f"but application requires head revision '{expected_head}'. "
                    "Run 'alembic upgrade head' to update database schema."
                )

            logger.info(
                f"Database schema '{schema}' validated at Alembic revision '{current_version}' (head: '{expected_head}')."
            )
            return current_version

    except DatabaseLifecycleError:
        raise
    except Exception as exc:
        logger.error(f"Database validation connection failed: {exc.__class__.__name__}")
        raise DatabaseConnectionError(
            f"Failed to connect to database for lifecycle validation: {exc.__class__.__name__}"
        ) from None
