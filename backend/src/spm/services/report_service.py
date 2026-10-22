"""The synchronous write path: validate -> guardrails -> persist (+ outbox) -> publish."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from spm.core.enums import Classification, ScoringState
from spm.core.errors import NotFound, SpmError
from spm.core.events import PriceReported, make_event
from spm.core.models import LocationFix, RawPriceInput, Report, Reporter, ShopPrice
from spm.ports import (
    Clock,
    DuplicateClientReport,
    IdGenerator,
    Metrics,
    OutboxRepo,
    ReportRepo,
)
from spm.services.guardrail_service import GuardrailService
from spm.services.outbox import OutboxPublisher

# A report counts as "verified" once scoring accepted it and its shop price is verified.
VERIFYING_CLASSIFICATIONS = {
    Classification.NORMAL,
    Classification.SHOP_OUTLIER,
    Classification.MARKET_SHIFT,
}


class _AssessmentStates(Protocol):
    async def get_state(self, report_id: UUID) -> tuple[ScoringState, Classification | None]: ...


class _ShopPrices(Protocol):
    async def shop_prices(self, product_id: str, area_id: str) -> list[ShopPrice]: ...


class ReportsUnitOfWork(Protocol):
    @property
    def reports(self) -> ReportRepo: ...
    @property
    def outbox(self) -> OutboxRepo: ...
    @property
    def assessments(self) -> _AssessmentStates: ...
    @property
    def bands(self) -> _ShopPrices: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SubmitReport:
    client_report_id: UUID
    product_id: str | None
    product_text: str | None
    shop_id: str
    price: Decimal
    quantity: Decimal
    unit: str
    observed_at: datetime
    location: LocationFix | None
    ip_hash: str | None


@dataclass(frozen=True, slots=True)
class SubmitResult:
    report: Report
    duplicate: bool  # a replay of the same client_report_id, or a near-identical recent report
    state: ScoringState = ScoringState.QUEUED


@dataclass(frozen=True, slots=True)
class ReportStatus:
    report_id: UUID
    state: ScoringState
    classification: Classification | None
    verified: bool


class ReportService:
    def __init__(
        self,
        guardrails: GuardrailService,
        uow_factory: Callable[[], AbstractAsyncContextManager[ReportsUnitOfWork]],
        outbox_publisher: OutboxPublisher,
        ids: IdGenerator,
        clock: Clock,
        metrics: Metrics | None = None,
    ) -> None:
        self._guardrails = guardrails
        self._uow_factory = uow_factory
        self._outbox = outbox_publisher
        self._ids = ids
        self._clock = clock
        self._metrics = metrics

    def _count(self, name: str, **dims: str) -> None:
        if self._metrics is not None:
            self._metrics.count(name, 1, **dims)

    async def submit(
        self,
        reporter: Reporter,
        cmd: SubmitReport,
        *,
        request_id: str | None,
        allow_backfill: bool = False,
    ) -> SubmitResult:
        try:
            result = await self._submit(reporter, cmd, request_id, allow_backfill)
        except SpmError as exc:
            self._count("reports_rejected", code=exc.code)
            raise
        self._count("reports_duplicate" if result.duplicate else "reports_received")
        return result

    async def _existing(self, report_id: UUID) -> SubmitResult:
        async with self._uow_factory() as uow:
            report = await uow.reports.get(report_id)
            assert report is not None, f"idempotency row points at missing report {report_id}"
            state, _ = await uow.assessments.get_state(report_id)
        return SubmitResult(report, duplicate=True, state=state)

    async def _submit(
        self, reporter: Reporter, cmd: SubmitReport, request_id: str | None, allow_backfill: bool
    ) -> SubmitResult:
        # 1. Replay of the same client_report_id (offline retry): return the stored report.
        async with self._uow_factory() as uow:
            replayed = await uow.reports.by_client_id(reporter.id, cmd.client_report_id)
        if replayed is not None:
            return await self._existing(replayed)

        # 2. Guardrails; a near-identical recent report is returned instead of storing another.
        admission = await self._guardrails.admit(
            reporter.id,
            RawPriceInput(cmd.product_id, cmd.product_text, cmd.price, cmd.quantity, cmd.unit),
            cmd.shop_id,
            cmd.observed_at,
            cmd.location,
            allow_backfill=allow_backfill,
            enforce_rate_limits=not allow_backfill,
        )
        if admission.duplicate_of is not None:
            return await self._existing(admission.duplicate_of)

        # 3. Build the report.
        report = Report(
            id=self._ids.new(),
            received_at=self._clock.now(),
            reporter_id=reporter.id,
            client_report_id=cmd.client_report_id,
            product_id=admission.normalized.product_id,
            shop_id=admission.shop.id,
            area_id=admission.shop.area_id,
            normalized=admission.normalized,
            observed_at=cmd.observed_at,
            location=cmd.location,
            distance_to_shop_m=admission.distance_m,
            location_verified=admission.location_verified,
            ip_hash=cmd.ip_hash,
        )
        event = make_event(
            "price.reported",
            PriceReported(
                report_id=report.id,
                received_at=report.received_at,
                product_id=report.product_id,
                shop_id=report.shop_id,
                area_id=report.area_id,
                reporter_id=report.reporter_id,
                norm_price=report.normalized.norm_price,
                observed_at=report.observed_at,
            ),
            "spm.api",
            request_id,
            self._clock,
            self._ids,
        )

        # 4. One transaction: report (+ idempotency row + queued assessment) and outbox event.
        async with self._uow_factory() as uow:
            try:
                await uow.reports.add(report)
            except DuplicateClientReport:
                await uow.rollback()
                stored = await uow.reports.by_client_id(reporter.id, cmd.client_report_id)
                assert stored is not None
                return await self._existing(stored)  # a concurrent retry won the race
            await uow.outbox.add([event])
            await uow.commit()

        # 5. Publish now; the sweeper retries if this fails.
        await self._outbox.flush([event])
        return SubmitResult(report, duplicate=False)

    async def status(self, reporter: Reporter, report_id: UUID) -> ReportStatus:
        """Scoring status of one of the reporter's own reports (others' are "not found")."""
        async with self._uow_factory() as uow:
            report = await uow.reports.get(report_id)
            if report is None or report.reporter_id != reporter.id:
                raise NotFound("Report not found.", report_id=str(report_id))
            state, classification = await uow.assessments.get_state(report_id)
            verified = False
            if classification in VERIFYING_CLASSIFICATIONS:
                prices = await uow.bands.shop_prices(report.product_id, report.area_id)
                verified = any(sp.shop_id == report.shop_id and sp.verified for sp in prices)
        return ReportStatus(report_id, state, classification, verified)
