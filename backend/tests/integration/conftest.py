"""Integration fixtures: a freshly migrated ``spm_test`` database and Redis DB 15 (MODULES.md §2.8).

Requires ``make up``. Override the targets with ``SPM_TEST_DATABASE_URL`` / ``SPM_TEST_REDIS_URL``.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from spm.adapters.postgres.db import make_session_factory
from spm.adapters.postgres.migrate import recreate_database, upgrade
from spm.adapters.postgres.tables import metadata

TEST_DATABASE_URL = os.environ.get(
    "SPM_TEST_DATABASE_URL", "postgresql+asyncpg://spm:spm@localhost:5433/spm_test"
)
TEST_REDIS_URL = os.environ.get("SPM_TEST_REDIS_URL", "redis://localhost:6380/15")


@pytest.fixture(scope="session")
def database_url() -> str:
    """A freshly recreated and migrated test database, once per test session."""
    asyncio.run(recreate_database(TEST_DATABASE_URL))
    upgrade("head", TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    # Pooled like production: a fresh connection costs ~100 ms through the Docker VM, a pooled
    # checkout ~4 ms. Overflow lets the concurrency tests open many sessions at once.
    engine = create_async_engine(database_url, pool_size=5, max_overflow=25)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory on the migrated DB. Every table is truncated after the test."""
    yield make_session_factory(engine)
    tables = ", ".join(t.name for t in metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    """Redis DB 15, flushed before and after each test."""
    client = aioredis.from_url(TEST_REDIS_URL)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
