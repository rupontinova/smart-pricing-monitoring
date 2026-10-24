import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from spm.adapters.memory import (
    FixedClock,
    MemoryBus,
    MemoryCatalogReader,
    MemoryRateLimiter,
    MemoryUnitOfWork,
    SequentialIds,
)
from spm.constants import RATE_LIMIT_PER_HOUR
from spm.core.errors import RateLimited
from spm.core.models import Reporter
from spm.services.catalog_cache import CatalogCache
from spm.services.guardrail_service import GuardrailService
from spm.services.outbox import OutboxPublisher
from spm.services.report_service import ReportService, SubmitReport

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)


def _service(uow: MemoryUnitOfWork, bus: MemoryBus) -> ReportService:
    clock = FixedClock(T0)
    guardrails = GuardrailService(
        CatalogCache(MemoryCatalogReader.from_seeds(), clock), MemoryRateLimiter(clock),
        lambda: uow, clock,
    )  # fmt: skip
    return ReportService(
        guardrails, lambda: uow, OutboxPublisher(bus, lambda: uow, clock), SequentialIds(), clock
    )


def _cmd(client_id: uuid.UUID) -> SubmitReport:
    return SubmitReport(
        client_id, "onion", None, "shop-a", Decimal(60), Decimal(1), "kg", T0, None, None
    )


async def test_concurrent_retry_losing_the_race_returns_the_winner() -> None:
    uow, bus = MemoryUnitOfWork(), MemoryBus()
    reporter = Reporter(uuid.uuid4(), "dev", 1, 1, 0, False, T0)
    client_id = uuid.uuid4()
    service = _service(uow, bus)
    winner = await service.submit(reporter, _cmd(client_id), request_id=None)

    # Simulate the race: the loser checked before the winner's idempotency row existed.
    real = uow.reports.by_client_id
    calls = 0

    async def stale_first(reporter_id: uuid.UUID, cid: uuid.UUID) -> Any:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real(reporter_id, cid)

    uow.reports.by_client_id = stale_first  # type: ignore[method-assign, assignment]
    loser = await service.submit(reporter, _cmd(client_id), request_id=None)
    assert loser.duplicate and loser.report.id == winner.report.id
    assert len(bus.published) == 1


async def test_publish_failure_still_accepts_the_report() -> None:
    uow, bus = MemoryUnitOfWork(), MemoryBus()
    bus.fail_publish = True
    reporter = Reporter(uuid.uuid4(), "dev", 1, 1, 0, False, T0)
    result = await _service(uow, bus).submit(reporter, _cmd(uuid.uuid4()), request_id="r1")
    assert not result.duplicate
    (row,) = uow.outbox.rows.values()
    assert row[1] is None  # unpublished: the sweeper will retry


async def test_backfill_skips_rate_limits_unless_enforced() -> None:
    uow, bus = MemoryUnitOfWork(), MemoryBus()
    service = _service(uow, bus)
    reporter = Reporter(uuid.uuid4(), "dev", 1, 1, 0, False, T0)
    for _ in range(RATE_LIMIT_PER_HOUR + 5):
        await service.submit(reporter, _cmd(uuid.uuid4()), request_id=None, allow_backfill=True)

    spammer = Reporter(uuid.uuid4(), "spam", 1, 1, 0, False, T0)
    for _ in range(RATE_LIMIT_PER_HOUR):
        await service.submit(
            spammer, _cmd(uuid.uuid4()), request_id=None, allow_backfill=True,
            enforce_rate_limits=True,
        )  # fmt: skip
    with pytest.raises(RateLimited):
        await service.submit(
            spammer, _cmd(uuid.uuid4()), request_id=None, allow_backfill=True,
            enforce_rate_limits=True,
        )  # fmt: skip
