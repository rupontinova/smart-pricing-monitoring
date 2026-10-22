"""Alembic environment (async, asyncpg).

URL: ``sqlalchemy.url`` when set (tests), else ``SPM_DATABASE_URL``.
"""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from spm.adapters.postgres.tables import include_name, metadata
from spm.config import get_settings

config = context.config


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if not url:
        raise RuntimeError("No database URL: set SPM_DATABASE_URL")
    return url


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=metadata,
        include_name=include_name,
        compare_type=True,
        transaction_per_migration=True,
    )


def run_migrations_offline() -> None:
    _configure(url=_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
