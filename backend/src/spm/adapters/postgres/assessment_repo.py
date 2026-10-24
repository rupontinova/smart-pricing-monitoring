"""``PgAssessmentRepo``: the mutable scoring view (``report_assessments``).

Observation reads join ``reporters`` for the current trust (α / (α + β)); all are served by the
``ra_*`` indexes.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import report_assessments, reporters
from spm.core.enums import Classification, ScoringState
from spm.core.models import Assessment, Observation

ra = report_assessments

_SETTLEABLE = (Classification.NORMAL, Classification.SHOP_OUTLIER, Classification.MARKET_SHIFT)


def _observation_query() -> Select[Any]:
    return select(
        ra.c.report_id,
        ra.c.reporter_id,
        ra.c.shop_id,
        ra.c.norm_price,
        ra.c.observed_at,
        (reporters.c.alpha / (reporters.c.alpha + reporters.c.beta)).label("trust"),
        ra.c.location_verified,
        ra.c.classification,
        ra.c.ip_hash,
    ).join(reporters, reporters.c.id == ra.c.reporter_id)


def _observation(row: Any) -> Observation:
    return Observation(
        report_id=row.report_id,
        reporter_id=row.reporter_id,
        shop_id=row.shop_id,
        norm_price=float(row.norm_price),
        observed_at=row.observed_at,
        trust=float(row.trust),
        location_verified=row.location_verified,
        classification=Classification(row.classification) if row.classification else None,
        ip_hash=row.ip_hash,
    )


def _money(x: float | None) -> Decimal | None:
    if x is None:
        return None
    return Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


class PgAssessmentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _observations(self, query: Select[Any]) -> list[Observation]:
        rows = await self._session.execute(query.order_by(ra.c.observed_at.desc()))
        return [_observation(r) for r in rows.all()]

    # ----------------------------------------------------------------------------- reads
    async def get_state(self, report_id: UUID) -> tuple[ScoringState, Classification | None]:
        row = (
            await self._session.execute(
                select(ra.c.scoring_state, ra.c.classification).where(ra.c.report_id == report_id)
            )
        ).first()
        if row is None:
            raise LookupError(f"no assessment for report {report_id}")
        return ScoringState(row[0]), Classification(row[1]) if row[1] is not None else None

    async def get_observation(self, report_id: UUID) -> Observation | None:
        row = (
            await self._session.execute(_observation_query().where(ra.c.report_id == report_id))
        ).first()
        return _observation(row) if row else None

    async def observations_for_area(
        self, product_id: str, area_id: str, since: datetime, exclude_shop: str | None = None
    ) -> list[Observation]:
        """Every report for the product in the area observed since ``since`` (all labels,
        unscored included; callers filter), newest first. Uses ``ra_area_idx``."""
        query = _observation_query().where(
            ra.c.product_id == product_id, ra.c.area_id == area_id, ra.c.observed_at >= since
        )
        if exclude_shop is not None:
            query = query.where(ra.c.shop_id != exclude_shop)
        return await self._observations(query)

    async def observations_for_shop(
        self, product_id: str, shop_id: str, since: datetime
    ) -> list[Observation]:
        return await self._observations(
            _observation_query().where(
                ra.c.product_id == product_id, ra.c.shop_id == shop_id, ra.c.observed_at >= since
            )
        )

    async def pending_for_shop(
        self, product_id: str, shop_id: str, since: datetime
    ) -> list[Observation]:
        """PENDING reports at the shop (``ra_pending_idx``)."""
        return await self._observations(
            _observation_query().where(
                ra.c.product_id == product_id,
                ra.c.shop_id == shop_id,
                ra.c.classification == Classification.PENDING,
                ra.c.observed_at >= since,
            )
        )

    async def unsettled_for_shop(
        self, product_id: str, shop_id: str, since: datetime
    ) -> list[Observation]:
        return await self._observations(
            _observation_query().where(
                ra.c.product_id == product_id,
                ra.c.shop_id == shop_id,
                ra.c.observed_at >= since,
                ra.c.scoring_state == ScoringState.SCORED,
                ra.c.classification.in_(_SETTLEABLE),
                ra.c.trust_settled.is_(False),
            )
        )

    # ---------------------------------------------------------------------------- writes
    async def save(self, assessment: Assessment, scored_at: datetime) -> None:
        await self._session.execute(
            update(ra)
            .where(ra.c.report_id == assessment.report_id)
            .values(
                scoring_state=ScoringState.SCORED,
                classification=assessment.classification,
                direction=assessment.direction,
                z_shop=assessment.z_shop,
                z_market=assessment.z_market,
                reference_price=_money(assessment.reference_price),
                insufficient_data=assessment.insufficient_data,
                reason=assessment.reason,
                scored_at=scored_at,
                updated_at=func.now(),
            )
        )

    async def resolve(
        self, report_id: UUID, classification: Classification, resolved_by: UUID, at: datetime
    ) -> None:
        await self._session.execute(
            update(ra)
            .where(ra.c.report_id == report_id)
            .values(classification=classification, resolved_by=resolved_by, updated_at=at)
        )

    async def mark_failed(self, report_id: UUID, reason: str, at: datetime) -> None:
        await self._session.execute(
            update(ra)
            .where(ra.c.report_id == report_id)
            .values(
                scoring_state=ScoringState.FAILED, reason=reason[:500], scored_at=at, updated_at=at
            )
        )

    async def mark_trust_settled(self, report_ids: Iterable[UUID]) -> None:
        ids = list(set(report_ids))
        if ids:
            await self._session.execute(
                update(ra).where(ra.c.report_id.in_(ids)).values(trust_settled=True)
            )
