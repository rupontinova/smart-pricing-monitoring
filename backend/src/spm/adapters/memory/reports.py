"""In-memory reporters, reports, assessments and shop prices, and a unit of work over them."""

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID

from spm.adapters.memory.events import MemoryOutboxRepo, MemoryProcessedEventRepo
from spm.constants import LAST_SEEN_UPDATE_INTERVAL_H
from spm.core.enums import Classification, ScoringState
from spm.core.models import Report, Reporter, ShopPrice
from spm.ports import DuplicateClientReport


@dataclass(frozen=True, slots=True)
class StoredReport:
    """The fields duplicate detection needs (tests may insert these directly)."""

    id: UUID
    reporter_id: UUID
    product_id: str
    shop_id: str
    norm_price: Decimal
    observed_at: datetime


class MemoryReporterRepo:
    def __init__(self) -> None:
        self.by_id: dict[UUID, Reporter] = {}

    async def get(self, reporter_id: UUID) -> Reporter | None:
        return self.by_id.get(reporter_id)

    async def get_by_device(self, device_id: str) -> Reporter | None:
        return next((r for r in self.by_id.values() if r.device_id == device_id), None)

    async def create(self, reporter: Reporter) -> None:
        if await self.get_by_device(reporter.device_id) is None:
            self.by_id[reporter.id] = reporter

    async def touch(self, reporter_id: UUID, at: datetime) -> None:
        r = self.by_id[reporter_id]
        if r.last_seen_at is None or r.last_seen_at < at - timedelta(
            hours=LAST_SEEN_UPDATE_INTERVAL_H
        ):
            self.by_id[reporter_id] = dataclasses.replace(r, last_seen_at=at)

    async def add_evidence(self, reporter_id: UUID, d_alpha: float, d_beta: float) -> None:
        r = self.by_id[reporter_id]
        self.by_id[reporter_id] = dataclasses.replace(
            r, alpha=r.alpha + d_alpha, beta=r.beta + d_beta
        )

    async def trust_many(self, ids: Iterable[UUID]) -> dict[UUID, float]:
        return {i: self.by_id[i].trust for i in set(ids) if i in self.by_id}

    def ban(self, reporter_id: UUID) -> None:
        self.by_id[reporter_id] = dataclasses.replace(self.by_id[reporter_id], banned=True)


class MemoryReportRepo:
    def __init__(self, reporters: MemoryReporterRepo | None = None) -> None:
        self.rows: list[StoredReport] = []
        self.by_id: dict[UUID, Report] = {}
        self.idempotency: dict[tuple[UUID, UUID], UUID] = {}
        self.states: dict[UUID, tuple[ScoringState, Classification | None]] = {}
        self.duplicate_queries = 0
        self._reporters = reporters

    async def add(self, report: Report) -> None:
        key = (report.reporter_id, report.client_report_id)
        if key in self.idempotency:
            raise DuplicateClientReport(str(report.client_report_id))
        self.idempotency[key] = report.id
        self.by_id[report.id] = report
        self.states[report.id] = (ScoringState.QUEUED, None)
        self.rows.append(
            StoredReport(report.id, report.reporter_id, report.product_id, report.shop_id,
                         report.normalized.norm_price, report.observed_at)
        )  # fmt: skip
        if self._reporters is not None and report.reporter_id in self._reporters.by_id:
            r = self._reporters.by_id[report.reporter_id]
            self._reporters.by_id[r.id] = dataclasses.replace(r, reports_total=r.reports_total + 1)

    async def by_client_id(self, reporter_id: UUID, client_report_id: UUID) -> UUID | None:
        return self.idempotency.get((reporter_id, client_report_id))

    async def find_recent_duplicate(
        self, reporter_id: UUID, product_id: str, shop_id: str, since: datetime
    ) -> list[tuple[UUID, Decimal]]:
        self.duplicate_queries += 1
        hits = [
            r
            for r in self.rows
            if (r.reporter_id, r.product_id, r.shop_id) == (reporter_id, product_id, shop_id)
            and r.observed_at >= since
        ]
        hits.sort(key=lambda r: r.observed_at, reverse=True)
        return [(r.id, r.norm_price) for r in hits]

    async def get(self, report_id: UUID) -> Report | None:
        return self.by_id.get(report_id)


class MemoryAssessmentRepo:
    """Read side for report status; scoring writes arrive with M10 to M13."""

    def __init__(self, reports: MemoryReportRepo) -> None:
        self._reports = reports

    async def get_state(self, report_id: UUID) -> tuple[ScoringState, Classification | None]:
        try:
            return self._reports.states[report_id]
        except KeyError:
            raise LookupError(f"no assessment for report {report_id}") from None

    def set_state(
        self, report_id: UUID, state: ScoringState, classification: Classification | None
    ) -> None:
        self._reports.states[report_id] = (state, classification)


class MemoryBandRepo:
    def __init__(self) -> None:
        self.prices: dict[tuple[str, str], ShopPrice] = {}  # (product_id, shop_id)

    async def shop_prices(self, product_id: str, area_id: str) -> list[ShopPrice]:
        return [
            sp
            for (pid, _), sp in self.prices.items()
            if pid == product_id and sp.area_id == area_id
        ]

    async def upsert_shop_price(self, sp: ShopPrice) -> None:
        self.prices[(sp.product_id, sp.shop_id)] = sp


class MemoryUnitOfWork:
    """Shares one set of memory repositories. Most writes apply immediately; processed-event marks
    are transactional (committed on ``commit``, discarded otherwise)."""

    def __init__(
        self,
        reports: MemoryReportRepo | None = None,
        outbox: MemoryOutboxRepo | None = None,
        processed: MemoryProcessedEventRepo | None = None,
        reporters: MemoryReporterRepo | None = None,
        bands: MemoryBandRepo | None = None,
    ) -> None:
        self.reporters = reporters or MemoryReporterRepo()
        self.reports = reports or MemoryReportRepo(self.reporters)
        self.assessments = MemoryAssessmentRepo(self.reports)
        self.bands = bands or MemoryBandRepo()
        self.outbox = outbox or MemoryOutboxRepo()
        self.processed = processed or MemoryProcessedEventRepo()
        self.commits = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.processed.rollback()  # anything not committed is discarded

    async def commit(self) -> None:
        self.processed.commit()
        self.commits += 1

    async def rollback(self) -> None:
        self.processed.rollback()
