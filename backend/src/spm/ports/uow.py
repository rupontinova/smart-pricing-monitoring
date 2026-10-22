from types import TracebackType
from typing import Protocol, Self

from spm.ports.events import OutboxRepo
from spm.ports.repos import (
    AlertRepo,
    AnomalyRepo,
    AssessmentRepo,
    BandRepo,
    PriceCheckRepo,
    ReporterRepo,
    ReportRepo,
    StatsRepo,
    SubscriptionRepo,
)


class UnitOfWork(Protocol):
    """Async context manager; every repository shares one transaction."""

    reporters: ReporterRepo
    reports: ReportRepo
    assessments: AssessmentRepo
    anomalies: AnomalyRepo
    bands: BandRepo
    stats: StatsRepo
    outbox: OutboxRepo
    subscriptions: SubscriptionRepo
    alerts: AlertRepo
    checks: PriceCheckRepo

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def advisory_lock(self, key: str) -> None:
        """Transaction-scoped lock."""
        ...
