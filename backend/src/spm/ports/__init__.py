"""Port interfaces (``typing.Protocol``). Services depend on these only; adapters implement them."""

from spm.ports.cache import HotCache, RateLimiter
from spm.ports.catalog import CatalogReader
from spm.ports.clock import Clock, IdGenerator
from spm.ports.events import EventPublisher, OutboxRepo
from spm.ports.metrics import Metrics
from spm.ports.notify import Notifier
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
from spm.ports.uow import UnitOfWork

__all__ = [
    "AlertRepo",
    "AnomalyRepo",
    "AssessmentRepo",
    "BandRepo",
    "CatalogReader",
    "Clock",
    "EventPublisher",
    "HotCache",
    "IdGenerator",
    "Metrics",
    "Notifier",
    "OutboxRepo",
    "PriceCheckRepo",
    "RateLimiter",
    "ReportRepo",
    "ReporterRepo",
    "StatsRepo",
    "SubscriptionRepo",
    "UnitOfWork",
]
