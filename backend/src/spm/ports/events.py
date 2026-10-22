"""Event ports. ``EventConsumer`` is added in M07."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from spm.core.events import Event


class EventPublisher(Protocol):
    async def publish(self, events: list[Event]) -> None: ...


class OutboxRepo(Protocol):
    async def add(self, events: list[Event]) -> None: ...
    async def mark_published(self, event_ids: list[UUID], at: datetime) -> None: ...
    async def unpublished(self, older_than: datetime, limit: int) -> list[Event]: ...
    async def bump_attempts(self, event_ids: list[UUID]) -> None: ...
