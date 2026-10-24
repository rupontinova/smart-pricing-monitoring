"""``PgAnomalyRepo``: open anomalies are unique per ``dedup_key`` (``anomalies_open_dedup``)."""

from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import anomalies
from spm.core.enums import AnomalyKind, Direction
from spm.core.models import Anomaly


def _money(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


class PgAnomalyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_if_absent(self, anomaly: Anomaly, dedup_key: str) -> bool:
        """Insert as open unless an open anomaly with this key exists (then False)."""
        stmt = (
            insert(anomalies)
            .values(
                id=anomaly.id,
                kind=anomaly.kind,
                direction=anomaly.direction,
                product_id=anomaly.product_id,
                area_id=anomaly.area_id,
                shop_id=anomaly.shop_id,
                observed_price=_money(anomaly.observed_price),
                reference_price=_money(anomaly.reference_price),
                magnitude_pct=anomaly.magnitude_pct,
                report_ids=list(anomaly.report_ids),
                dedup_key=dedup_key,
                detected_at=anomaly.detected_at,
            )
            .on_conflict_do_nothing(
                index_elements=[anomalies.c.dedup_key], index_where=text("status = 'open'")
            )
            .returning(anomalies.c.id)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def recent(self, product_id: str, area_id: str, since: datetime) -> list[Anomaly]:
        rows = await self._session.execute(
            select(anomalies)
            .where(
                anomalies.c.product_id == product_id,
                anomalies.c.area_id == area_id,
                anomalies.c.detected_at >= since,
            )
            .order_by(anomalies.c.detected_at.desc())
        )
        return [
            Anomaly(
                id=r.id,
                kind=AnomalyKind(r.kind),
                direction=Direction(r.direction),
                product_id=r.product_id,
                area_id=r.area_id,
                shop_id=r.shop_id,
                observed_price=float(r.observed_price),
                reference_price=float(r.reference_price),
                magnitude_pct=float(r.magnitude_pct),
                report_ids=tuple(r.report_ids),
                detected_at=r.detected_at,
            )
            for r in rows.all()
        ]
