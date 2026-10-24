"""Programmatic Alembic and database setup: ``spm migrate``, the test fixtures, the simulator."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# src/spm/adapters/postgres/migrate.py -> backend/alembic.ini
ALEMBIC_INI = Path(__file__).resolve().parents[4] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config; ``database_url`` overrides ``SPM_DATABASE_URL``."""
    config = Config(str(ALEMBIC_INI))
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(revision: str = "head", database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(revision: str, database_url: str | None = None) -> None:
    command.downgrade(alembic_config(database_url), revision)


async def recreate_database(url: str) -> None:
    """DROP + CREATE the database (via the maintenance db ``spm``) with PostGIS enabled.

    Clones ``template_postgis`` (shipped by the postgis image) when present: ~10x faster than
    ``CREATE EXTENSION postgis`` in an empty database.
    """
    target = make_url(url)
    admin = create_async_engine(
        target.set(database="spm"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with admin.connect() as conn:
            has_template = (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = 'template_postgis'")
                )
            ).scalar()
            template = " TEMPLATE template_postgis" if has_template else ""
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{target.database}"{template}'))
    finally:
        await admin.dispose()
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    finally:
        await engine.dispose()
