import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from spm.adapters.postgres.tables import anomalies, reports
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.core.enums import AnomalyKind, Direction

pytestmark = pytest.mark.integration

PARTITION_COUNT = text("SELECT count(*) FROM pg_inherits WHERE inhparent = 'reports'::regclass")

# Drops monthly partitions past the migration's +12 month horizon (cleanup after extending it).
DROP_PARTITIONS_BEYOND_HORIZON = text("""
DO $$
DECLARE
  horizon text := to_char(date_trunc('month', now() AT TIME ZONE 'UTC') + interval '12 months',
                          '"reports_y"YYYY"m"MM');
  r record;
BEGIN
  FOR r IN SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
           WHERE i.inhparent = 'reports'::regclass
             AND c.relname LIKE 'reports_y%' AND c.relname > horizon
  LOOP
    EXECUTE format('DROP TABLE %I', r.relname);
  END LOOP;
END $$
""")


def _report_row(received_at: datetime) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "received_at": received_at,
        "reporter_id": uuid.uuid4(),
        "client_report_id": uuid.uuid4(),
        "product_id": "onion",
        "shop_id": "shop-a",
        "area_id": "demo-central",
        "raw_price": 60,
        "raw_quantity": 1,
        "raw_unit": "kg",
        "norm_price": 60,
        "base_quantity": 1,
        "observed_at": received_at,
        "location_verified": False,
    }


def _month_partition(at: datetime) -> str:
    return f"reports_y{at:%Y}m{at:%m}"


async def _partitions_of(session: AsyncSession, ids: list[object]) -> dict[object, str]:
    result = await session.execute(
        text("SELECT id, tableoid::regclass::text FROM reports WHERE id = ANY(:ids)"),
        {"ids": ids},
    )
    return {row_id: partition for row_id, partition in result.all()}


async def _insert_and_locate(
    session_factory: async_sessionmaker[AsyncSession], *received: datetime
) -> list[str]:
    """Insert one report per timestamp; return the partition each row landed in, in order."""
    rows = [_report_row(at) for at in received]
    ids = [row["id"] for row in rows]
    async with session_factory() as session:
        await session.execute(insert(reports), rows)
        await session.commit()
        located = await _partitions_of(session, ids)
    return [located[row_id] for row_id in ids]


async def test_reports_routed_to_monthly_partition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    partitions = await _insert_and_locate(
        session_factory,
        now,
        next_month,  # lower bound is inclusive
        next_month - timedelta(microseconds=1),  # upper bound is exclusive
        datetime(2020, 1, 15, tzinfo=UTC),  # before any monthly partition
    )
    assert partitions == [
        _month_partition(now),
        _month_partition(next_month),
        _month_partition(month_start),
        "reports_default",
    ]


async def test_ensure_partitions_is_idempotent(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        first = (await conn.execute(text("SELECT spm_ensure_report_partitions(3)"))).scalar_one()
        second = (await conn.execute(text("SELECT spm_ensure_report_partitions(3)"))).scalar_one()
        count = (await conn.execute(PARTITION_COUNT)).scalar_one()
    assert first == 0  # the migration already created current .. +12
    assert second == 0
    assert count == 14  # 13 monthly + default


async def test_ensure_partitions_moves_rows_out_of_default(
    session_factory: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    beyond = datetime.now(UTC).replace(day=15) + timedelta(days=31 * 14)  # past the +12 horizon
    (before,) = await _insert_and_locate(session_factory, beyond)
    assert before == "reports_default"

    try:
        async with engine.begin() as conn:
            created = (
                await conn.execute(text("SELECT spm_ensure_report_partitions(15)"))
            ).scalar_one()
            located = (
                await conn.execute(text("SELECT tableoid::regclass::text FROM reports"))
            ).scalar_one()
            in_default = (await conn.execute(text("SELECT count(*) FROM reports_default"))).scalar()
        assert created == 3  # +13, +14, +15
        assert located == _month_partition(beyond)
        assert in_default == 0
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM reports"))
            await conn.execute(DROP_PARTITIONS_BEYOND_HORIZON)


def _anomaly(dedup_key: str) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "kind": AnomalyKind.SHOP_OUTLIER,
        "direction": Direction.UP,
        "product_id": "onion",
        "area_id": "demo-central",
        "shop_id": "shop-a",
        "report_ids": [uuid.uuid4()],
        "dedup_key": dedup_key,
        "detected_at": datetime.now(UTC),
    }


async def test_open_anomaly_dedup_rejects_second_open_allows_after_resolve(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = "SHOP_OUTLIER:demo-central:onion:shop-a:up"
    first = _anomaly(key)
    async with session_factory() as session:
        await session.execute(insert(anomalies), [first])
        await session.commit()

        with pytest.raises(IntegrityError, match="anomalies_open_dedup"):
            await session.execute(insert(anomalies), [_anomaly(key)])
        await session.rollback()

        await session.execute(
            update(anomalies)
            .where(anomalies.c.id == first["id"])
            .values(status="resolved", resolved_at=datetime.now(UTC))
        )
        await session.execute(insert(anomalies), [_anomaly(key)])
        await session.commit()
        result = await session.execute(select(anomalies.c.status).order_by(anomalies.c.detected_at))
        statuses = result.scalars().all()
    assert statuses == ["resolved", "open"]


async def test_advisory_lock_blocks_second_transaction_until_commit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    acquired = asyncio.Event()

    async def second_holder() -> None:
        async with PgUnitOfWork(session_factory) as uow:
            await uow.advisory_lock("band:demo-central:onion")
            acquired.set()
            await uow.commit()

    async with PgUnitOfWork(session_factory) as first:
        await first.advisory_lock("band:demo-central:onion")
        task = asyncio.create_task(second_holder())
        await asyncio.sleep(0.5)
        assert not acquired.is_set(), "second transaction got the lock while the first held it"

        # A different key is not blocked.
        async with PgUnitOfWork(session_factory) as other:
            await asyncio.wait_for(other.advisory_lock("band:demo-central:garlic"), timeout=2)

        await first.commit()
        await asyncio.wait_for(acquired.wait(), timeout=5)
    await task


async def test_uow_rolls_back_uncommitted_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with PgUnitOfWork(session_factory) as uow:
        await uow.session.execute(insert(anomalies), [_anomaly("k1")])
    async with PgUnitOfWork(session_factory) as uow:
        await uow.session.execute(insert(anomalies), [_anomaly("k2")])
        await uow.commit()
    async with session_factory() as session:
        keys = (await session.execute(select(anomalies.c.dedup_key))).scalars().all()
    assert keys == ["k2"]


async def test_uow_session_outside_context_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError):
        _ = PgUnitOfWork(session_factory).session
