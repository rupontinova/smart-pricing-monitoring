import asyncio

import pytest

from spm.adapters.memory import MemoryBus
from spm.core.events import Event
from tests.unit.events_helpers import price_reported


async def test_publish_then_consume() -> None:
    bus = MemoryBus()
    got: list[Event] = []

    async def handler(event: Event) -> None:
        got.append(event)

    bus.subscribe("scoring", {"price.reported"}, handler)
    events = [price_reported(i) for i in range(3)]
    await bus.publish(events)
    assert await bus.drain() == 3
    assert got == events


async def test_each_group_gets_every_event_and_types_filter() -> None:
    bus = MemoryBus()
    a: list[Event] = []
    b: list[Event] = []

    async def to_a(event: Event) -> None:
        a.append(event)

    async def to_b(event: Event) -> None:
        b.append(event)

    bus.subscribe("a", {"price.reported"}, to_a)
    bus.subscribe("b", {"band.updated"}, to_b)  # not interested
    await bus.publish([price_reported()])
    await bus.drain()
    assert len(a) == 1 and b == []


async def test_group_only_sees_events_after_subscribing() -> None:
    bus = MemoryBus()
    got: list[Event] = []

    async def handler(event: Event) -> None:
        got.append(event)

    await bus.publish([price_reported(0)])
    bus.subscribe("late", {"price.reported"}, handler)
    await bus.publish([price_reported(1)])
    await bus.drain()
    assert [e.data["report_id"] for e in got] == [price_reported(1).data["report_id"]]


async def test_failing_handler_retried_5_times_then_dead_lettered() -> None:
    bus = MemoryBus()
    attempts = 0

    async def poison(event: Event) -> None:
        nonlocal attempts
        attempts += 1
        raise ValueError("bad payload")

    bus.subscribe("scoring", {"price.reported"}, poison)
    await bus.publish([price_reported()])
    await bus.drain()
    assert attempts == 5
    (dead,) = bus.dlq
    assert (dead.group, dead.attempts) == ("scoring", 5)
    assert "bad payload" in dead.error


async def test_transient_failure_redelivered_without_dlq() -> None:
    bus = MemoryBus()
    calls = 0

    async def flaky(event: Event) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("db blip")

    bus.subscribe("scoring", {"price.reported"}, flaky)
    await bus.publish([price_reported()])
    await bus.drain()
    assert calls == 3 and bus.dlq == []


async def test_run_consumes_until_stopped() -> None:
    bus = MemoryBus()
    got: list[Event] = []
    stop = asyncio.Event()

    async def handler(event: Event) -> None:
        got.append(event)

    task = asyncio.create_task(bus.run("scoring", {"price.reported"}, handler, stop=stop))
    await asyncio.sleep(0)
    await bus.publish([price_reported(0), price_reported(1)])
    for _ in range(50):
        if len(got) == 2:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert len(got) == 2


async def test_publish_failure_raises() -> None:
    bus = MemoryBus()
    bus.fail_publish = True
    with pytest.raises(ConnectionError):
        await bus.publish([price_reported()])
