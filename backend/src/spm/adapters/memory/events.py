"""In-memory event plumbing: bus, outbox and processed-event store (tests and the simulator)."""

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import structlog

from spm.constants import EVENT_MAX_DELIVERIES
from spm.core.events import Event
from spm.ports import Handler

log = structlog.get_logger("spm.bus.memory")


@dataclass(slots=True)
class _Delivery:
    event: Event
    attempts: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class _Group:
    types: set[str]
    handler: Handler
    queue: deque[_Delivery] = field(default_factory=deque)


@dataclass(frozen=True, slots=True)
class DeadLetter:
    group: str
    event: Event
    attempts: int
    error: str


class MemoryBus:
    """``EventPublisher`` + ``EventConsumer`` in one process.

    Groups receive only events published after they subscribe (like ``XGROUP CREATE … $``).
    A failing handler is retried up to ``max_deliveries`` times, then the event goes to ``dlq``.
    """

    def __init__(self, max_deliveries: int = EVENT_MAX_DELIVERIES) -> None:
        self.max_deliveries = max_deliveries
        self.published: list[Event] = []
        self.dlq: list[DeadLetter] = []
        self._groups: dict[str, _Group] = {}
        self._wakeup = asyncio.Event()
        self.fail_publish = False  # tests: simulate an unavailable bus

    async def publish(self, events: list[Event]) -> None:
        if self.fail_publish:
            raise ConnectionError("memory bus unavailable")
        for event in events:
            self.published.append(event)
            for group in self._groups.values():
                group.queue.append(_Delivery(event))
        self._wakeup.set()

    def subscribe(self, group: str, types: set[str], handler: Handler) -> None:
        self._groups[group] = _Group(set(types), handler)

    async def _process_one(self, name: str, group: _Group) -> bool:
        if not group.queue:
            return False
        delivery = group.queue.popleft()
        if delivery.event.type not in group.types:
            return True  # acknowledged and skipped
        delivery.attempts += 1
        try:
            await group.handler(delivery.event)
        except Exception as exc:
            delivery.last_error = repr(exc)
            if delivery.attempts >= self.max_deliveries:
                self.dlq.append(DeadLetter(name, delivery.event, delivery.attempts, repr(exc)))
                log.warning("event_dead_lettered", group=name, event_id=str(delivery.event.id))
            else:
                group.queue.append(delivery)  # redeliver later
        return True

    async def drain(self) -> int:
        """Deliver until every group's queue is empty (deterministic tests). Returns deliveries."""
        delivered = 0
        progress = True
        while progress:
            progress = False
            for name, group in list(self._groups.items()):
                if await self._process_one(name, group):
                    delivered += 1
                    progress = True
        return delivered

    async def run(
        self, group: str, types: set[str], handler: Handler, *, stop: asyncio.Event
    ) -> None:
        self.subscribe(group, types, handler)
        while not stop.is_set():
            if not await self._process_one(group, self._groups[group]):
                self._wakeup.clear()
                waiter = asyncio.ensure_future(self._wakeup.wait())
                stopper = asyncio.ensure_future(stop.wait())
                await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                for task in (waiter, stopper):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task


class MemoryOutboxRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, tuple[Event, datetime | None, int]] = {}

    async def add(self, events: list[Event]) -> None:
        for e in events:
            self.rows.setdefault(e.id, (e, None, 0))

    async def mark_published(self, event_ids: list[UUID], at: datetime) -> None:
        for eid in event_ids:
            event, _, attempts = self.rows[eid]
            self.rows[eid] = (event, at, attempts)

    async def unpublished(self, older_than: datetime, limit: int) -> list[Event]:
        due = [
            e for e, published, _ in self.rows.values()
            if published is None and e.occurred_at < older_than
        ]  # fmt: skip
        return sorted(due, key=lambda e: e.occurred_at)[:limit]

    async def bump_attempts(self, event_ids: list[UUID]) -> None:
        for eid in event_ids:
            event, published, attempts = self.rows[eid]
            self.rows[eid] = (event, published, attempts + 1)


class MemoryProcessedEventRepo:
    """Marks are staged until the owning ``MemoryUnitOfWork`` commits, like a DB transaction, so a
    handler failure leaves the event unprocessed and a redelivery runs it again."""

    def __init__(self) -> None:
        self.marked: dict[tuple[str, UUID], datetime] = {}
        self._pending: dict[tuple[str, UUID], datetime] = {}

    async def seen(self, consumer: str, event_id: UUID) -> bool:
        return (consumer, event_id) in self.marked

    async def mark(self, consumer: str, event_id: UUID, at: datetime) -> bool:
        key = (consumer, event_id)
        if key in self.marked or key in self._pending:
            return False
        self._pending[key] = at
        return True

    def commit(self) -> None:
        self.marked.update(self._pending)
        self._pending.clear()

    def rollback(self) -> None:
        self._pending.clear()
