"""``PgStatsRepo``: daily rollups and the 90-day area median."""

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import price_stats_daily, report_assessments
from spm.core.enums import Classification
from spm.core.models import DailyStat

psd = price_stats_daily
_AREA_INCLUDED = (Classification.NORMAL, Classification.MARKET_SHIFT)


def _money(x: float | None) -> Decimal | None:
    if x is None:
        return None
    return Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


class PgStatsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_daily(self, rows: list[DailyStat]) -> None:
        if not rows:
            return
        values = [
            {
                "product_id": r.product_id,
                "area_id": r.area_id,
                "shop_id": r.shop_id,
                "day": r.day,
                "p25": _money(r.p25),
                "p50": _money(r.p50),
                "p75": _money(r.p75),
                "mad": _money(r.mad),
                "n_reports": r.n_reports,
                "total_weight": r.total_weight,
                "final": r.final,
            }
            for r in rows
        ]
        stmt = insert(psd).values(values)
        updated = ("p25", "p50", "p75", "mad", "n_reports", "total_weight", "final")
        await self._session.execute(
            stmt.on_conflict_do_update(
                # psd_uniq is an expression index: (product, area, COALESCE(shop, '*'), day).
                # '*' must be a literal: as a bind parameter it matches the index only while
                # Postgres uses custom plans; from the 6th execution of the prepared statement
                # the generic plan can't infer the index ("no unique constraint matching").
                index_elements=[
                    psd.c.product_id,
                    psd.c.area_id,
                    func.coalesce(psd.c.shop_id, literal_column("'*'")),
                    psd.c.day,
                ],
                set_={k: stmt.excluded[k] for k in updated},
                where=psd.c.final.is_(False),  # finalized days are never rewritten by live scoring
            )
        )

    async def series(
        self, product_id: str, area_id: str, start: date, end: date, shop_id: str | None = None
    ) -> list[DailyStat]:
        query = select(psd).where(
            psd.c.product_id == product_id,
            psd.c.area_id == area_id,
            psd.c.day >= start,
            psd.c.day <= end,
            psd.c.shop_id.is_(None) if shop_id is None else psd.c.shop_id == shop_id,
        )
        rows = await self._session.execute(query.order_by(psd.c.day))
        return [
            DailyStat(
                product_id=r.product_id,
                area_id=r.area_id,
                shop_id=r.shop_id,
                day=r.day,
                p25=float(r.p25),
                p50=float(r.p50),
                p75=float(r.p75),
                mad=float(r.mad),
                n_reports=r.n_reports,
                total_weight=float(r.total_weight),
                final=r.final,
            )
            for r in rows.all()
        ]

    async def area_median(
        self, product_id: str, area_id: str, since: datetime
    ) -> tuple[float | None, int]:
        """Unweighted median of included (NORMAL / MARKET_SHIFT) reports since ``since``."""
        ra = report_assessments
        row = (
            await self._session.execute(
                select(
                    func.percentile_cont(0.5).within_group(ra.c.norm_price),
                    func.count(),
                ).where(
                    ra.c.product_id == product_id,
                    ra.c.area_id == area_id,
                    ra.c.observed_at >= since,
                    ra.c.classification.in_(_AREA_INCLUDED),
                )
            )
        ).one()
        return (float(row[0]) if row[0] is not None else None), int(row[1])
