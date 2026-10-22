"""Event ports: publishing, consuming, the transactional outbox and consumer idempotency."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from spm.core.events import Event

Handler = Callable[[Event], Awaitable[None]]


class EventPublisher(Protocol):
    async def publish(self, events: list[Event]) -> None:
        """Raise if any event could not be published (the outbox then retries)."""
        ...


class EventConsumer(Protocol):
    async def run(
        self, group: str, types: set[str], handler: Handler, *, stop: asyncio.Event
    ) -> None:
        """Deliver events of ``types`` to ``handler`` (at least once) until ``stop`` is set."""
        ...


class OutboxRepo(Protocol):
    async def add(self, events: list[Event]) -> None: ...
    async def mark_published(self, event_ids: list[UUID], at: datetime) -> None: ...
    async def unpublished(self, older_than: datetime, limit: int) -> list[Event]: ...
    async def bump_attempts(self, event_ids: list[UUID]) -> None: ...


class ProcessedEventRepo(Protocol):
    async def seen(self, consumer: str, event_id: UUID) -> bool: ...
    async def mark(self, consumer: str, event_id: UUID, at: datetime) -> bool:
        """Record the event as processed; False if it already was (another delivery won)."""
        ...
