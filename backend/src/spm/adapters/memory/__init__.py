"""In-memory adapters for unit tests, e2e tests and the simulator."""

from spm.adapters.memory.catalog import MemoryCatalogReader
from spm.adapters.memory.clock import FixedClock
from spm.adapters.memory.events import (
    DeadLetter,
    MemoryBus,
    MemoryOutboxRepo,
    MemoryProcessedEventRepo,
)
from spm.adapters.memory.geo import MemoryShopGeoIndex
from spm.adapters.memory.ids import SequentialIds
from spm.adapters.memory.metrics import MemoryMetrics, MetricCall
from spm.adapters.memory.rate_limiter import MemoryRateLimiter
from spm.adapters.memory.reports import (
    MemoryAssessmentRepo,
    MemoryBandRepo,
    MemoryReporterRepo,
    MemoryReportRepo,
    MemoryUnitOfWork,
    StoredReport,
)

__all__ = [
    "DeadLetter",
    "FixedClock",
    "MemoryAssessmentRepo",
    "MemoryBandRepo",
    "MemoryBus",
    "MemoryCatalogReader",
    "MemoryMetrics",
    "MemoryOutboxRepo",
    "MemoryProcessedEventRepo",
    "MemoryRateLimiter",
    "MemoryReportRepo",
    "MemoryReporterRepo",
    "MemoryShopGeoIndex",
    "MemoryUnitOfWork",
    "MetricCall",
    "SequentialIds",
    "StoredReport",
]
