"""Consumer runtime: run consumer groups until SIGINT/SIGTERM, and make handlers idempotent."""

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from spm.container import Container
from spm.core.events import EVENT_TYPES, Event
from spm.ports import Clock, Handler, ProcessedEventRepo
from spm.services.outbox import OutboxSweeper

log = structlog.get_logger("spm.worker")


@dataclass(frozen=True, slots=True)
class ConsumerSpec:
    group: str
    handler: Handler
    types: set[str] = field(default_factory=lambda: set(EVENT_TYPES))


class IdempotentUnitOfWork(Protocol):
    @property
    def processed(self) -> ProcessedEventRepo: ...
    async def commit(self) -> None: ...


AfterCommit = Callable[[], Awaitable[None]]


def idempotent[U: IdempotentUnitOfWork](
    consumer_name: str,
    handler: Callable[[Event, U], Awaitable[AfterCommit | None]],
    *,
    uow_factory: Callable[[], AbstractAsyncContextManager[U]],
    clock: Clock,
) -> Handler:
    """Wrap ``handler(event, uow)`` so each event id is processed once per consumer.

    The event is marked processed inside the handler's own transaction, before the handler runs:
    side effects and the mark commit (or roll back) together, and a concurrent duplicate delivery
    blocks on the mark and then skips. A handler may return an ``AfterCommit`` callback (e.g. to
    publish outbox events or refresh a cache), which runs only once the commit succeeded.
    """

    async def wrapped(event: Event) -> None:
        async with uow_factory() as uow:
            if await uow.processed.seen(consumer_name, event.id):
                log.debug("event_already_processed", consumer=consumer_name, event_id=str(event.id))
                return
            if not await uow.processed.mark(consumer_name, event.id, clock.now()):
                return  # another delivery committed first
            after_commit = await handler(event, uow)
            await uow.commit()
        if after_commit is not None:
            await after_commit()

    return wrapped


async def log_event(event: Event) -> None:
    """Debug consumer: prints every event (``spm worker --debug-log-events``)."""
    log.info(
        "event",
        event_type=event.type,
        event_id=str(event.id),
        source=event.source,
        correlation_id=event.correlation_id,
        data=event.data,
    )


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)


async def run_consumers(
    container: Container, specs: list[ConsumerSpec], *, stop: asyncio.Event | None = None
) -> None:
    """Run every consumer until ``stop`` is set (SIGINT/SIGTERM set it)."""
    stop = stop or asyncio.Event()
    _install_signal_handlers(stop)

    consumer = container.consumer
    if consumer is None:
        raise RuntimeError(f"SPM_EVENT_BUS={container.settings.event_bus} can't consume events")

    # Locally the worker also sweeps the outbox once on start (on AWS the scheduler does, M17).
    if container.session_factory is not None and container.bus is not None:
        from spm.adapters.postgres.uow import PgUnitOfWork

        session_factory = container.session_factory
        sweeper = OutboxSweeper(
            container.bus, lambda: PgUnitOfWork(session_factory), container.clock
        )
        try:
            await sweeper.sweep()
        except Exception:
            log.warning("startup_outbox_sweep_failed", exc_info=True)

    log.info("worker_started", consumers=[s.group for s in specs])
    async with asyncio.TaskGroup() as tasks:
        for spec in specs:
            tasks.create_task(consumer.run(spec.group, spec.types, spec.handler, stop=stop))
    log.info("worker_stopped")
