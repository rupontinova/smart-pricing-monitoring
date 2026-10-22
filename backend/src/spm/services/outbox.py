"""Transactional outbox: services add events to the outbox in their business transaction, then
``OutboxPublisher.flush`` publishes them right after commit. Anything that didn't make it (bus
down, process killed) is picked up by ``OutboxSweeper``. Delivery is therefore at least once,
and consumers are idempotent (``workers.runtime.idempotent``)."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol

import structlog

from spm.constants import OUTBOX_SWEEP_AGE_S, OUTBOX_SWEEP_BATCH
from spm.core.events import Event
from spm.ports import Clock, EventPublisher, OutboxRepo

log = structlog.get_logger("spm.outbox")


class OutboxUnitOfWork(Protocol):
    """The slice of ``UnitOfWork`` the outbox services use."""

    @property
    def outbox(self) -> OutboxRepo: ...
    async def commit(self) -> None: ...


UowFactory = Callable[[], AbstractAsyncContextManager[OutboxUnitOfWork]]


class OutboxPublisher:
    def __init__(self, publisher: EventPublisher, uow_factory: UowFactory, clock: Clock) -> None:
        self._publisher = publisher
        self._uow_factory = uow_factory
        self._clock = clock

    async def flush(self, events: list[Event]) -> bool:
        """Publish events whose outbox rows were just committed, then mark them published.

        Never raises: on failure the rows stay unpublished and the sweeper retries them.
        Returns whether the events were published and marked.
        """
        if not events:
            return True
        ids = [e.id for e in events]
        try:
            await self._publisher.publish(events)
            async with self._uow_factory() as uow:
                await uow.outbox.mark_published(ids, self._clock.now())
                await uow.commit()
        except Exception:
            log.warning("outbox_flush_failed", event_ids=[str(i) for i in ids], exc_info=True)
            return False
        return True


class OutboxSweeper:
    def __init__(
        self,
        publisher: EventPublisher,
        uow_factory: UowFactory,
        clock: Clock,
        *,
        age_s: float = OUTBOX_SWEEP_AGE_S,
        batch: int = OUTBOX_SWEEP_BATCH,
    ) -> None:
        self._publisher = publisher
        self._uow_factory = uow_factory
        self._clock = clock
        self._age = timedelta(seconds=age_s)
        self._batch = batch

    async def sweep(self) -> int:
        """Republish rows unpublished for longer than ``age_s``, batch by batch. Returns the count.

        Each batch's rows stay locked while it is published, so concurrent sweepers don't
        double-publish. On a publish failure the batch's attempts are bumped and the sweep stops.
        """
        total = 0
        while True:
            async with self._uow_factory() as uow:
                events = await uow.outbox.unpublished(self._clock.now() - self._age, self._batch)
                if not events:
                    return total
                ids = [e.id for e in events]
                try:
                    await self._publisher.publish(events)
                except Exception:
                    await uow.outbox.bump_attempts(ids)
                    await uow.commit()
                    log.warning("outbox_sweep_publish_failed", count=len(ids), exc_info=True)
                    return total
                await uow.outbox.mark_published(ids, self._clock.now())
                await uow.commit()
            total += len(events)
            log.info("outbox_swept", count=len(events))
            if len(events) < self._batch:
                return total
