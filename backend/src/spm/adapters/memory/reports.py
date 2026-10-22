"""In-memory report storage: duplicate lookups (M06); M08 adds the rest of ``ReportRepo``."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StoredReport:
    id: UUID
    reporter_id: UUID
    product_id: str
    shop_id: str
    norm_price: Decimal
    observed_at: datetime


class MemoryReportRepo:
    def __init__(self) -> None:
        self.rows: list[StoredReport] = []
        self.duplicate_queries = 0

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
        return [
            (r.id, r.norm_price) for r in sorted(hits, key=lambda r: r.observed_at, reverse=True)
        ]


class MemoryUnitOfWork:
    """Shares one set of memory repositories; commit/rollback are no-ops for now."""

    def __init__(self, reports: MemoryReportRepo | None = None) -> None:
        self.reports = reports or MemoryReportRepo()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None
