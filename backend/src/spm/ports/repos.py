"""Repository ports. All share one transaction through ``UnitOfWork``."""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from spm.core.enums import Classification, ScoringState
from spm.core.models import (
    Anomaly,
    Assessment,
    DailyStat,
    FairBand,
    Observation,
    Report,
    Reporter,
    ShopPrice,
)


class DuplicateClientReport(Exception):
    """``ReportRepo.add``: this reporter already stored a report with this ``client_report_id``."""


class ReporterRepo(Protocol):
    async def get(self, reporter_id: UUID) -> Reporter | None: ...
    async def get_by_device(self, device_id: str) -> Reporter | None: ...
    async def create(self, reporter: Reporter) -> None:
        """Insert; a no-op if the device already has a reporter (concurrent first logins)."""
        ...

    async def touch(self, reporter_id: UUID, at: datetime) -> None:
        """Set ``last_seen_at`` unless it was set within the last hour."""
        ...

    async def add_evidence(self, reporter_id: UUID, d_alpha: float, d_beta: float) -> None: ...
    async def trust_many(self, ids: Iterable[UUID]) -> dict[UUID, float]: ...


class ReportRepo(Protocol):
    async def add(self, report: Report) -> None:
        """Also inserts the idempotency row and a ``queued`` assessment, and counts the report on
        the reporter. Raises ``DuplicateClientReport`` if the idempotency row already exists."""
        ...

    async def by_client_id(self, reporter_id: UUID, client_report_id: UUID) -> UUID | None: ...
    async def find_recent_duplicate(
        self, reporter_id: UUID, product_id: str, shop_id: str, since: datetime
    ) -> list[tuple[UUID, Decimal]]: ...
    async def get(self, report_id: UUID) -> Report | None: ...


class AssessmentRepo(Protocol):
    async def observations_for_shop(
        self, product_id: str, shop_id: str, since: datetime
    ) -> list[Observation]: ...
    async def observations_for_area(
        self, product_id: str, area_id: str, since: datetime, exclude_shop: str | None = None
    ) -> list[Observation]: ...
    async def pending_for_shop(
        self, product_id: str, shop_id: str, since: datetime
    ) -> list[Observation]: ...
    async def save(self, assessment: Assessment, scored_at: datetime) -> None: ...
    async def mark_trust_settled(self, report_ids: Iterable[UUID]) -> None: ...
    async def get_state(self, report_id: UUID) -> tuple[ScoringState, Classification | None]: ...


class AnomalyRepo(Protocol):
    async def open_if_absent(self, anomaly: Anomaly, dedup_key: str) -> bool:
        """False if a matching anomaly is already open."""
        ...

    async def recent(self, product_id: str, area_id: str, since: datetime) -> list[Anomaly]: ...


class BandRepo(Protocol):
    async def get_band(self, product_id: str, area_id: str) -> FairBand | None: ...
    async def upsert_band(self, band: FairBand) -> None: ...
    async def get_rebase(self, product_id: str, area_id: str) -> datetime | None: ...
    async def set_rebase(self, product_id: str, area_id: str, at: datetime) -> None: ...
    async def upsert_shop_price(self, sp: ShopPrice) -> None: ...
    async def shop_prices(self, product_id: str, area_id: str) -> list[ShopPrice]: ...


class StatsRepo(Protocol):
    async def upsert_daily(self, rows: list[DailyStat]) -> None: ...
    async def series(
        self, product_id: str, area_id: str, start: date, end: date, shop_id: str | None = None
    ) -> list[DailyStat]: ...
    async def area_median(
        self, product_id: str, area_id: str, since: datetime
    ) -> tuple[float | None, int]: ...


# Placeholders: their methods are specified by the modules that first need them.
class SubscriptionRepo(Protocol):
    """Defined in M16 (create, list_for_reporter, deactivate, matching)."""


class AlertRepo(Protocol):
    """Defined in M16 (add, inbox, mark_read, mark_delivered)."""


class PriceCheckRepo(Protocol):
    """Defined in M15/M18 (add)."""
