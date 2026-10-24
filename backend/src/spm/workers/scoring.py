"""The scoring consumer: ``price.reported`` -> ``ScoringService`` (idempotent per event)."""

from typing import Any

from spm.container import Container
from spm.core.events import Event
from spm.services.band_service import BandService
from spm.services.outbox import OutboxPublisher
from spm.services.scoring_service import ScoringService
from spm.workers.runtime import AfterCommit, ConsumerSpec, idempotent

GROUP = "scoring"


def build_scoring_service(container: Container) -> ScoringService:
    if (
        container.catalog is None
        or container.uow_factory is None
        or container.hot_cache is None
        or container.bus is None
    ):
        raise RuntimeError("scoring needs a database, Redis (hot cache) and an event bus")
    uow_factory = container.uow_factory
    return ScoringService(
        container.catalog,
        BandService(container.hot_cache, uow_factory, container.clock),
        OutboxPublisher(container.bus, uow_factory, container.clock),
        container.clock,
        container.ids,
        container.metrics,
    )


def scoring_consumer(container: Container) -> ConsumerSpec:
    service = build_scoring_service(container)
    assert container.uow_factory is not None

    async def handle(event: Event, uow: Any) -> AfterCommit:
        outcome = await service.handle(event, uow)

        async def after() -> None:
            await service.after_commit(outcome)

        return after

    handler = idempotent(GROUP, handle, uow_factory=container.uow_factory, clock=container.clock)
    return ConsumerSpec(GROUP, handler, {"price.reported"})


def consumer_specs(container: Container) -> list[ConsumerSpec]:
    """Every business consumer (alerts arrive in M16, metrics in M18)."""
    return [scoring_consumer(container)]
