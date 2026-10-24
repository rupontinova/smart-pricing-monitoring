import asyncio
import os
import signal
from datetime import UTC, datetime

import pytest

from spm.adapters.memory import FixedClock, MemoryBus, MemoryUnitOfWork, SequentialIds
from spm.config import Settings
from spm.container import Container
from spm.core.events import Event
from spm.workers.runtime import ConsumerSpec, idempotent, run_consumers
from tests.unit.events_helpers import price_reported

CLOCK = FixedClock(datetime(2026, 10, 10, tzinfo=UTC))


async def test_idempotent_processes_same_event_once() -> None:
    uow = MemoryUnitOfWork()
    handled: list[Event] = []

    async def handler(event: Event, _uow: MemoryUnitOfWork) -> None:
        handled.append(event)

    wrapped = idempotent("scoring", handler, uow_factory=lambda: uow, clock=CLOCK)
    event = price_reported()
    for _ in range(3):
        await wrapped(event)
    await wrapped(price_reported(1, ids=SequentialIds(epoch=datetime(2027, 1, 1, tzinfo=UTC))))
    assert len(handled) == 2
    assert await uow.processed.seen("scoring", event.id)
    assert not await uow.processed.seen("alerts", event.id)  # per consumer


async def test_idempotent_failed_handler_is_not_marked() -> None:
    uow = MemoryUnitOfWork()
    calls = 0

    async def flaky(event: Event, _uow: MemoryUnitOfWork) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("db blip")

    wrapped = idempotent("scoring", flaky, uow_factory=lambda: uow, clock=CLOCK)
    event = price_reported()
    with pytest.raises(ConnectionError):
        await wrapped(event)
    assert not await uow.processed.seen("scoring", event.id)
    await wrapped(event)  # the redelivery runs it again
    await wrapped(event)  # and now it's a duplicate
    assert calls == 2


def _container(settings: Settings, bus: MemoryBus | None) -> Container:
    return Container(settings=settings, clock=CLOCK, ids=SequentialIds(), bus=bus, consumer=bus)


async def test_run_consumers_until_sigterm(settings: Settings) -> None:
    bus = MemoryBus()
    got: list[Event] = []

    async def handler(event: Event) -> None:
        got.append(event)

    task = asyncio.create_task(
        run_consumers(_container(settings, bus), [ConsumerSpec("g", handler)])
    )
    await asyncio.sleep(0.05)
    await bus.publish([price_reported()])
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0.01)
    os.kill(os.getpid(), signal.SIGTERM)  # handled by run_consumers: graceful stop
    await asyncio.wait_for(task, timeout=2)
    assert len(got) == 1


async def test_run_consumers_needs_a_consumable_bus(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="can't consume"):
        await run_consumers(_container(settings, None), [])


async def test_after_commit_runs_only_after_a_successful_commit() -> None:
    uow = MemoryUnitOfWork()
    ran: list[int] = []

    async def handler(event: Event, _uow: MemoryUnitOfWork):  # type: ignore[no-untyped-def]
        async def after() -> None:
            ran.append(uow.commits)  # the commit already happened

        return after

    await idempotent("g", handler, uow_factory=lambda: uow, clock=CLOCK)(price_reported())
    assert ran == [1]

    async def failing(event: Event, _uow: MemoryUnitOfWork):  # type: ignore[no-untyped-def]
        async def after() -> None:
            ran.append(-1)

        raise ConnectionError("before commit")
        return after  # pragma: no cover

    with pytest.raises(ConnectionError):
        await idempotent("g2", failing, uow_factory=lambda: uow, clock=CLOCK)(price_reported())
    assert ran == [1]
