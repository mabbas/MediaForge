"""Unit and integration tests for FastAPI database lifecycle, Alembic revision validation,
and least-privilege role separation.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.config import get_api_settings
from api.database.connection import (
    Base,
    close_database,
    get_session,
    init_database,
)
from api.database.validator import (
    DatabaseLifecycleError,
    DatabaseRevisionError,
    DatabaseSchemaError,
    get_expected_alembic_head,
    validate_database_revision,
)
from api.main import app


@pytest.fixture(autouse=True)
def ensure_postgresql_environment():
    """Ensure PostgreSQL settings are active even if previous tests injected SQLite."""
    from dotenv import dotenv_values
    from src.env_loader import _project_root

    root = _project_root()
    deploy_env = root / "deploy" / ".env"
    prev_val = os.environ.get("GID_API_DATABASE_URL")
    if deploy_env.is_file():
        vals = dotenv_values(str(deploy_env))
        if "GID_API_DATABASE_URL" in vals and vals["GID_API_DATABASE_URL"]:
            os.environ["GID_API_DATABASE_URL"] = vals["GID_API_DATABASE_URL"]
    get_api_settings.cache_clear()
    yield
    if deploy_env.is_file():
        vals = dotenv_values(str(deploy_env))
        if "GID_API_DATABASE_URL" in vals and vals["GID_API_DATABASE_URL"]:
            os.environ["GID_API_DATABASE_URL"] = vals["GID_API_DATABASE_URL"]
    get_api_settings.cache_clear()


def test_get_expected_alembic_head():
    """Verify that get_expected_alembic_head dynamically inspects Alembic scripts."""
    head = get_expected_alembic_head()
    assert head == "8b3c2d1e0f4a"


@pytest.mark.asyncio
async def test_startup_succeeds_with_expected_revision():
    """Read-only revision check succeeds against authoritative migrated database."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        current_rev = await validate_database_revision(
            engine=engine, schema=settings.database_schema
        )
        assert current_rev == "8b3c2d1e0f4a"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_startup_fails_with_missing_schema():
    """Validation fails when schema does not exist."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        with pytest.raises(DatabaseSchemaError, match="does not exist"):
            await validate_database_revision(engine=engine, schema="missing_schema_xyz")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_startup_fails_with_incompatible_revision():
    """Validation fails when database revision does not match application head."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        with patch(
            "api.database.validator.get_expected_alembic_head",
            return_value="incompatible_rev_9999",
        ):
            with pytest.raises(DatabaseRevisionError, match="Database revision mismatch"):
                await validate_database_revision(
                    engine=engine, schema=settings.database_schema
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_startup_never_invokes_create_all():
    """Verify that init_database strictly avoids calling Base.metadata.create_all."""
    settings = get_api_settings()
    with patch.object(Base.metadata, "create_all") as mock_create_all:
        await init_database(
            database_url=settings.database_url,
            schema=settings.database_schema,
            validate_revision=True,
        )
        try:
            mock_create_all.assert_not_called()
        finally:
            await close_database()


@pytest.mark.asyncio
async def test_runtime_account_dml_allowed():
    """Verify runtime account gid_app can perform SELECT, INSERT, UPDATE, DELETE."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            # 1. SELECT
            res = await conn.execute(
                text("SELECT count(*) FROM gid.tenants WHERE slug = 'default'")
            )
            count = res.scalar()
            assert count >= 1

            # 2. INSERT
            await conn.execute(
                text(
                    "INSERT INTO gid.tenants (id, name, slug, tier, is_active) "
                    "VALUES ('00000000-0000-0000-0000-000000000088', 'DML Test', 'dml-test', 'free', true)"
                )
            )

            # 3. UPDATE
            await conn.execute(
                text(
                    "UPDATE gid.tenants SET name = 'Updated DML Test' WHERE slug = 'dml-test'"
                )
            )

            # 4. DELETE
            await conn.execute(
                text("DELETE FROM gid.tenants WHERE slug = 'dml-test'")
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_account_ddl_denied():
    """Verify runtime account gid_app is denied CREATE TABLE, ALTER TABLE, and DROP TABLE."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        # 1. CREATE TABLE in gid schema must fail
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                await conn.execute(text("CREATE TABLE gid.unauthorized_tbl (id int)"))
            await conn.rollback()

        # 2. CREATE TABLE in public schema must fail
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                await conn.execute(text("CREATE TABLE public.unauthorized_tbl (id int)"))
            await conn.rollback()

        # 3. DROP TABLE must fail (not table owner)
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="must be owner"):
                await conn.execute(text("DROP TABLE gid.tenants"))
            await conn.rollback()

        # 4. ALTER TABLE must fail (not table owner)
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="must be owner"):
                await conn.execute(text("ALTER TABLE gid.tenants ADD COLUMN unauthorized text"))
            await conn.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_lifecycle_close_and_reopen():
    """Verify proper connection lifecycle management and engine disposal."""
    settings = get_api_settings()
    import api.database.connection as conn_mod

    await init_database(settings.database_url, validate_revision=True)
    assert conn_mod.engine is not None
    assert conn_mod.async_session_factory is not None

    await close_database()
    assert conn_mod.engine is None


def test_fastapi_lifespan_against_postgresql():
    """Verify that full FastAPI lifespan starts and validates successfully against PostgreSQL."""
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
