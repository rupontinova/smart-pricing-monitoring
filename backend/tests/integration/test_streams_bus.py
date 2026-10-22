import asyncio
import contextlib
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

import pytest
import redis.asyncio as aioredis

from spm.adapters.memory import SequentialIds
from spm.adapters.redis.streams_bus import RedisStreamsBus
from spm.core.events import Event
from tests.integration.conftest import TEST_REDIS_URL
from tests.unit.events_helpers import price_reported

pytestmark = pytest.mark.integration

TYPES = {"price.reported"}


@pytest.fixture
async def client(redis_client: aioredis.Redis) -> AsyncIterator[aioredis.Redis]:
    """A decode_responses client like the app's (redis_client flushes DB 15 around the test)."""
    app_client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield app_client
    await app_client.aclose()


def fast_bus(client: aioredis.Redis, name: str) -> RedisStreamsBus:
    return RedisStreamsBus(
        client, consumer_name=name, block_ms=100, claim_interval_s=0.1, claim_min_idle_ms=50
    )


@contextlib.asynccontextmanager
async def consuming(
    bus: RedisStreamsBus, group: str, handler: Callable[[Event], Awaitable[None]]
) -> AsyncIterator[None]:
    await bus.ensure_group(group)
    stop = asyncio.Event()
    task = asyncio.create_task(bus.run(group, TYPES, handler, stop=stop))
    try:
        yield
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=3)


async def until(predicate: Callable[[], bool | Awaitable[bool]], limit_s: float = 5.0) -> None:
    """Poll external (Redis/consumer-task) state until ``predicate`` holds."""
    async with asyncio.timeout(limit_s):
        while True:
            result = predicate()
            if await result if inspect.isawaitable(result) else result:
                return
            await asyncio.sleep(0.02)


async def test_two_groups_each_receive_every_event(client: aioredis.Redis) -> None:
    got: dict[str, list[Event]] = {"scoring": [], "metrics": []}

    def collector(group: str) -> Callable[[Event], Awaitable[None]]:
        async def handle(event: Event) -> None:
            got[group].append(event)

        return handle

    ids = SequentialIds()
    events = [price_reported(i, ids=ids) for i in range(3)]
    async with (
        consuming(fast_bus(client, "c1"), "scoring", collector("scoring")),
        consuming(fast_bus(client, "c2"), "metrics", collector("metrics")),
    ):
        await RedisStreamsBus(client).publish(events)
        await until(lambda: len(got["scoring"]) == 3 and len(got["metrics"]) == 3)
    assert got["scoring"] == events and got["metrics"] == events
    for group in got:
        assert (await client.xpending(RedisStreamsBus(client).stream, group))["pending"] == 0


async def test_crash_before_ack_is_reclaimed_and_redelivered(client: aioredis.Redis) -> None:
    bus = fast_bus(client, "survivor")
    await bus.ensure_group("scoring")
    await RedisStreamsBus(client).publish([price_reported()])

    # A consumer that read the entry and then died before ACKing it.
    await client.xreadgroup("scoring", "crashed", {bus.stream: ">"}, count=10)
    assert (await client.xpending(bus.stream, "scoring"))["pending"] == 1

    got: list[Event] = []

    async def handle(event: Event) -> None:
        got.append(event)

    async with consuming(bus, "scoring", handle):
        await until(lambda: len(got) == 1)
    assert (await client.xpending(bus.stream, "scoring"))["pending"] == 0


async def test_poison_message_dead_lettered_after_5_attempts(client: aioredis.Redis) -> None:
    attempts = 0

    async def poison(event: Event) -> None:
        nonlocal attempts
        attempts += 1
        raise ValueError("cannot score this")

    bus = fast_bus(client, "c1")
    async with consuming(bus, "scoring", poison):
        await RedisStreamsBus(client).publish([price_reported()])

        async def dead_lettered() -> bool:
            return bool(await client.xlen(bus.dlq))

        await until(dead_lettered)
    assert attempts == 5
    entries = cast(list[tuple[str, dict[str, str]]], await client.xrange(bus.dlq))
    ((_, fields),) = entries
    assert fields["group"] == "scoring"
    assert fields["deliveries"] == "5"
    assert "cannot score this" in fields["error"]
    assert Event.from_json(fields["body"]).type == "price.reported"
    assert (await client.xpending(bus.stream, "scoring"))["pending"] == 0


async def test_uninteresting_types_are_acked_and_skipped(client: aioredis.Redis) -> None:
    got: list[Event] = []

    async def handle(event: Event) -> None:
        got.append(event)

    bus = fast_bus(client, "c1")
    async with consuming(bus, "metrics", handle):
        event = price_reported()
        tick = Event(event.id, "daily.tick", 1, event.occurred_at, "spm.scheduler", None, {})
        await RedisStreamsBus(client).publish([tick, event])
        await until(lambda: len(got) == 1)
    assert got[0].type == "price.reported"
    assert (await client.xpending(bus.stream, "metrics"))["pending"] == 0
