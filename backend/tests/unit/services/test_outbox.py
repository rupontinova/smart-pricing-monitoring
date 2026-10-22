from datetime import timedelta

from spm.adapters.memory import (
    FixedClock,
    MemoryBus,
    MemoryOutboxRepo,
    MemoryUnitOfWork,
    SequentialIds,
)
from spm.services.outbox import OutboxPublisher, OutboxSweeper
from tests.unit.events_helpers import T0, price_reported


def _setup() -> tuple[FixedClock, MemoryBus, MemoryOutboxRepo, MemoryUnitOfWork]:
    outbox = MemoryOutboxRepo()
    return FixedClock(T0), MemoryBus(), outbox, MemoryUnitOfWork(outbox=outbox)


async def test_flush_publishes_and_marks() -> None:
    clock, bus, outbox, uow = _setup()
    events = [price_reported(i, clock=clock) for i in range(2)]
    await outbox.add(events)
    assert await OutboxPublisher(bus, lambda: uow, clock).flush(events)
    assert bus.published == events
    assert all(published == T0 for _, published, _ in outbox.rows.values())


async def test_flush_failure_is_swallowed_and_rows_stay_unpublished() -> None:
    clock, bus, outbox, uow = _setup()
    bus.fail_publish = True
    events = [price_reported(clock=clock)]
    await outbox.add(events)
    assert not await OutboxPublisher(bus, lambda: uow, clock).flush(events)
    assert all(published is None for _, published, _ in outbox.rows.values())


async def test_sweeper_republishes_only_old_unpublished_rows() -> None:
    clock, bus, outbox, uow = _setup()
    ids = SequentialIds()
    old = price_reported(0, clock=FixedClock(T0 - timedelta(seconds=60)), ids=ids)
    fresh = price_reported(1, clock=FixedClock(T0 - timedelta(seconds=10)), ids=ids)
    await outbox.add([old, fresh])
    sweeper = OutboxSweeper(bus, lambda: uow, clock)
    assert await sweeper.sweep() == 1
    assert bus.published == [old]
    assert await sweeper.sweep() == 0  # marked, so not republished


async def test_sweeper_batches_and_bumps_attempts_on_failure() -> None:
    clock, bus, outbox, uow = _setup()
    ids = SequentialIds()
    old_clock = FixedClock(T0 - timedelta(minutes=5))
    await outbox.add([price_reported(i, clock=old_clock, ids=ids) for i in range(5)])

    bus.fail_publish = True
    assert await OutboxSweeper(bus, lambda: uow, clock, batch=2).sweep() == 0
    assert sorted(attempts for *_, attempts in outbox.rows.values()) == [0, 0, 0, 1, 1]

    bus.fail_publish = False
    assert await OutboxSweeper(bus, lambda: uow, clock, batch=2).sweep() == 5
    assert len(bus.published) == 5
