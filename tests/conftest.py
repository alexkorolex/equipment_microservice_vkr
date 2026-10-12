from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest

from src.etc.settings_microservice.base import SettingsLoader
from src.infrastructure.db import Database
from tests.common import LOGGER, ensure_test_env

ensure_test_env()


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    """A clean ``equipment`` schema in the test database (see tests/common.py)."""
    database = Database(SettingsLoader().load(), LOGGER)
    try:
        database.pool = await database.create_pool()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL for integration tests is unavailable: {exc}")
    await database.drop_schemas()
    await database.apply_schema()
    assert database.pool is not None
    try:
        yield database.pool
    finally:
        await database.close()
