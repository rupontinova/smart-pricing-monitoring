import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.memory import FixedClock, MemoryBus, SequentialIds
from spm.adapters.postgres.tables import anomalies, outbox
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.core.enums import AnomalyKind
from spm.core.events import Event
from spm.services.outbox import OutboxPublisher, OutboxSweeper
from spm.workers.runtime import idempotent
from tests.unit.events_helpers import price_reported

pytestmark = pytest.mark.integration


def _anomaly(key: str) -> dict[str, object]:
    return {
        "id": uuid.uuid4(), "kind": AnomalyKind.BAD_REPORT, "product_id": "onion",
        "area_id": "demo-central", "report_ids": [uuid.uuid4()], "dedup_key": key,
        "detected_at": datetime.now(UTC),
    }  # fmt: skip


async def _count(sf: async_sessionmaker[AsyncSession], table: object) -> int:
    async with sf() as s:
        return int((await s.execute(select(func.count()).select_from(table))).scalar_one())  # type: ignore[arg-type]


async def test_outbox_row_shares_the_business_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with PgUnitOfWork(session_factory) as uow:
        await uow.session.execute(insert(anomalies), [_anomaly("rolled-back")])
        await uow.outbox.add([price_reported(0)])
        await uow.rollback()
    assert await _count(session_factory, anomalies) == 0
    assert await _count(session_factory, outbox) == 0

    async with PgUnitOfWork(session_factory) as uow:
        await uow.session.execute(insert(anomalies), [_anomaly("committed")])
        await uow.outbox.add([price_reported(1)])
        await uow.commit()
    assert await _count(session_factory, anomalies) == 1
    assert await _count(session_factory, outbox) == 1


async def test_outbox_round_trips_events(session_factory: async_sessionmaker[AsyncSession]) -> None:
    event = price_reported()
    async with PgUnitOfWork(session_factory) as uow:
        await uow.outbox.add([event, event])  # duplicate add is ignored (unique event_id)
        await uow.commit()
    async with PgUnitOfWork(session_factory) as uow:
        assert await uow.outbox.unpublished(event.occurred_at + timedelta(seconds=1), 10) == [event]


async def test_sweeper_republishes_and_marks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    ids = SequentialIds()
    old = [
        price_reported(i, clock=FixedClock(now - timedelta(seconds=90)), ids=ids) for i in range(3)
    ]
    fresh = price_reported(9, clock=FixedClock(now - timedelta(seconds=5)), ids=ids)
    async with PgUnitOfWork(session_factory) as uow:
        await uow.outbox.add([*old, fresh])
        await uow.commit()

    bus = MemoryBus()
    sweeper = OutboxSweeper(bus, lambda: PgUnitOfWork(session_factory), FixedClock(now))
    assert await sweeper.sweep() == 3
    assert bus.published == old
    assert await sweeper.sweep() == 0

    async with session_factory() as s:
        rows = (await s.execute(select(outbox.c.event_id, outbox.c.published_at))).all()
    published = {eid for eid, at in rows if at is not None}
    assert published == {e.id for e in old}


async def test_sweeper_failure_bumps_attempts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with PgUnitOfWork(session_factory) as uow:
        await uow.outbox.add([price_reported(clock=FixedClock(now - timedelta(minutes=2)))])
        await uow.commit()
    bus = MemoryBus()
    bus.fail_publish = True
    assert (
        await OutboxSweeper(bus, lambda: PgUnitOfWork(session_factory), FixedClock(now)).sweep()
        == 0
    )
    async with session_factory() as s:
        assert (await s.execute(select(outbox.c.attempts))).scalar_one() == 1


async def test_flush_marks_published(session_factory: async_sessionmaker[AsyncSession]) -> None:
    event = price_reported()
    async with PgUnitOfWork(session_factory) as uow:
        await uow.outbox.add([event])
        await uow.commit()
    bus = MemoryBus()
    flushed = await OutboxPublisher(
        bus, lambda: PgUnitOfWork(session_factory), FixedClock(datetime.now(UTC))
    ).flush([event])
    assert flushed and bus.published == [event]
    async with session_factory() as s:
        assert (await s.execute(select(outbox.c.published_at))).scalar_one() is not None


async def test_idempotent_on_postgres_side_effects_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls = 0

    async def handler(event: Event, uow: PgUnitOfWork) -> None:
        nonlocal calls
        calls += 1
        await uow.session.execute(insert(anomalies), [_anomaly(f"from-{event.id}")])
        if calls == 1:
            raise ConnectionError("crash after the side effect")

    wrapped = idempotent(
        "scoring", handler, uow_factory=lambda: PgUnitOfWork(session_factory),
        clock=FixedClock(datetime.now(UTC)),
    )  # fmt: skip
    event = price_reported()
    with pytest.raises(ConnectionError):
        await wrapped(event)
    assert await _count(session_factory, anomalies) == 0  # side effect rolled back with the mark
    await wrapped(event)
    await wrapped(event)
    assert calls == 2
    assert await _count(session_factory, anomalies) == 1
