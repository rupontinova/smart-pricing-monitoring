"""``PgReportRepo``. M06 implements ``find_recent_duplicate``; the rest of ``ReportRepo`` is M08."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import reports
from spm.constants import OBSERVED_AT_FUTURE_SKEW_MIN


class PgReportRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_recent_duplicate(
        self, reporter_id: UUID, product_id: str, shop_id: str, since: datetime
    ) -> list[tuple[UUID, Decimal]]:
        """(report_id, norm_price) of this reporter's reports for the product and shop observed
        since ``since``, newest first."""
        query = (
            select(reports.c.id, reports.c.norm_price)
            .where(
                reports.c.reporter_id == reporter_id,
                reports.c.product_id == product_id,
                reports.c.shop_id == shop_id,
                reports.c.observed_at >= since,
                # received_at >= observed_at - skew always holds, so this bound is redundant for
                # correctness but lets Postgres prune old monthly partitions.
                reports.c.received_at >= since - timedelta(minutes=OBSERVED_AT_FUTURE_SKEW_MIN),
            )
            .order_by(reports.c.observed_at.desc())
        )
        rows = await self._session.execute(query)
        return [(report_id, price) for report_id, price in rows.all()]
