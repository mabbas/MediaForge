"""Shared test fixtures.

API tests use SQLite for fast, isolated testing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def use_test_database(tmp_path):
    """Override database URL for tests (SQLite in pytest tmp dir)."""
    db_path = (tmp_path / "test.db").as_posix()
    with patch.dict(
        "os.environ",
        {"GID_API_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"},
    ):
        yield
