"""Port interfaces (``typing.Protocol``). Services depend on these only; adapters implement them."""

from spm.ports.cache import HotCache, RateLimiter, ShopGeoIndex
from spm.ports.catalog import CatalogReader
from spm.ports.clock import Clock, IdGenerator
from spm.ports.events import (
    EventConsumer,
    EventPublisher,
    Handler,
    OutboxRepo,
    ProcessedEventRepo,
)
from spm.ports.metrics import Metrics
from spm.ports.notify import Notifier
from spm.ports.repos import (
    AlertRepo,
    AnomalyRepo,
    AssessmentRepo,
    BandRepo,
    DuplicateClientReport,
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
    "DuplicateClientReport",
    "EventConsumer",
    "EventPublisher",
    "Handler",
    "HotCache",
    "IdGenerator",
    "Metrics",
    "Notifier",
    "OutboxRepo",
    "PriceCheckRepo",
    "ProcessedEventRepo",
    "RateLimiter",
    "ReportRepo",
    "ReporterRepo",
    "ShopGeoIndex",
    "StatsRepo",
    "SubscriptionRepo",
    "UnitOfWork",
]
