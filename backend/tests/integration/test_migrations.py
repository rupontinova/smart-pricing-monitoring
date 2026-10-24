import asyncio
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from spm.adapters.postgres.migrate import downgrade, recreate_database, upgrade
from spm.adapters.postgres.tables import include_name, metadata
from tests.integration.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "areas", "products", "product_aliases", "shops", "reporters", "reports", "report_idempotency",
    "report_assessments", "anomalies", "band_state", "shop_prices", "price_stats_daily",
    "price_checks", "subscriptions", "digests", "alerts", "outbox", "processed_events",
}  # fmt: skip


async def _scalar(url: str, sql: str) -> object:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar()
    finally:
        await engine.dispose()


def _app_tables(url: str) -> set[str]:
    rows = asyncio.run(
        _scalar(
            url,
            "SELECT coalesce(array_agg(tablename::text), '{}') FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename NOT LIKE 'reports_y%' AND tablename NOT IN "
            "('reports_default', 'spatial_ref_sys', 'alembic_version')",
        )
    )
    assert isinstance(rows, list)
    return set(rows)


def test_upgrade_downgrade_upgrade_round_trip() -> None:
    # Sync test: Alembic runs its own event loop. Uses a throwaway database.
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/spm_test_migrations"
    asyncio.run(recreate_database(url))

    upgrade("head", url)
    assert _app_tables(url) == EXPECTED_TABLES

    downgrade("base", url)
    assert _app_tables(url) == set()
    assert (
        asyncio.run(_scalar(url, "SELECT count(*) FROM pg_type WHERE typname = 'classification'"))
        == 0
    )

    upgrade("head", url)
    assert _app_tables(url) == EXPECTED_TABLES


async def test_tables_metadata_matches_migrated_schema(engine: AsyncEngine) -> None:
    def diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(
            conn,
            opts={
                "include_name": include_name,
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        return list(compare_metadata(context, metadata))

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_migration_creates_current_through_plus_12_month_partitions(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as conn:
        names = (
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                        "WHERE i.inhparent = 'reports'::regclass ORDER BY 1"
                    )
                )
            )
            .scalars()
            .all()
        )
    now = datetime.now(UTC)
    first = f"reports_y{now:%Y}m{now:%m}"
    monthly = [n for n in names if n.startswith("reports_y")]
    assert "reports_default" in names
    assert len(monthly) == 13
    assert monthly[0] == first
