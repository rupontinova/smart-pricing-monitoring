"""``PgReportRepo``: immutable raw reports, plus their idempotency row and queued assessment."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from geoalchemy2 import Geometry
from sqlalchemy import cast, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import (
    report_assessments,
    report_idempotency,
    reporters,
    reports,
)
from spm.constants import OBSERVED_AT_FUTURE_SKEW_MIN
from spm.core.enums import BaseUnit
from spm.core.models import GeoPoint, LocationFix, NormalizedPrice, RawPriceInput, Report
from spm.core.units import BASE_DIMENSION, UNITS, parse_unit
from spm.ports import DuplicateClientReport

BASE_QUANTITY_QUANTUM = Decimal("0.0001")  # reports.base_quantity numeric(12,4)


class PgReportRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, report: Report) -> None:
        # The idempotency row goes first: a concurrent submit of the same client_report_id
        # blocks on it and then gets DuplicateClientReport instead of a constraint error.
        claimed = await self._session.execute(
            pg_insert(report_idempotency)
            .values(
                reporter_id=report.reporter_id,
                client_report_id=report.client_report_id,
                report_id=report.id,
                received_at=report.received_at,
            )
            .on_conflict_do_nothing()
            .returning(report_idempotency.c.report_id)
        )
        if claimed.first() is None:
            raise DuplicateClientReport(str(report.client_report_id))

        n = report.normalized
        fix = report.location
        await self._session.execute(
            insert(reports).values(
                id=report.id,
                received_at=report.received_at,
                reporter_id=report.reporter_id,
                client_report_id=report.client_report_id,
                product_id=report.product_id,
                shop_id=report.shop_id,
                area_id=report.area_id,
                raw_product_text=n.raw.product_text,
                raw_price=n.raw.price,
                raw_quantity=n.raw.quantity,
                raw_unit=n.raw.unit,
                norm_price=n.norm_price,
                base_quantity=n.base_quantity.quantize(BASE_QUANTITY_QUANTUM),
                observed_at=report.observed_at,
                location=(
                    func.ST_GeogFromText(f"SRID=4326;POINT({fix.point.lng!r} {fix.point.lat!r})")
                    if fix
                    else None
                ),
                location_accuracy_m=fix.accuracy_m if fix else None,
                distance_to_shop_m=report.distance_to_shop_m,
                location_verified=report.location_verified,
                ip_hash=report.ip_hash,
            )
        )
        await self._session.execute(
            insert(report_assessments).values(
                report_id=report.id,
                received_at=report.received_at,
                reporter_id=report.reporter_id,
                product_id=report.product_id,
                shop_id=report.shop_id,
                area_id=report.area_id,
                norm_price=n.norm_price,
                observed_at=report.observed_at,
                location_verified=report.location_verified,
                ip_hash=report.ip_hash,
            )
        )
        await self._session.execute(
            update(reporters)
            .where(reporters.c.id == report.reporter_id)
            .values(reports_total=reporters.c.reports_total + 1)
        )

    async def by_client_id(self, reporter_id: UUID, client_report_id: UUID) -> UUID | None:
        result = await self._session.execute(
            select(report_idempotency.c.report_id).where(
                report_idempotency.c.reporter_id == reporter_id,
                report_idempotency.c.client_report_id == client_report_id,
            )
        )
        return result.scalar_one_or_none()

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

    async def get(self, report_id: UUID) -> Report | None:
        query = select(
            reports,
            func.ST_Y(cast(reports.c.location, Geometry)).label("lat"),
            func.ST_X(cast(reports.c.location, Geometry)).label("lng"),
        ).where(reports.c.id == report_id)
        row = (await self._session.execute(query)).mappings().first()
        return _report(dict(row)) if row else None


def _report(r: dict[str, Any]) -> Report:
    raw = RawPriceInput(
        product_id=r["product_id"] if r["raw_product_text"] is None else None,
        product_text=r["raw_product_text"],
        price=r["raw_price"],
        quantity=r["raw_quantity"],
        unit=r["raw_unit"],
    )
    location = (
        LocationFix(GeoPoint(r["lat"], r["lng"]), r["location_accuracy_m"])
        if r["lat"] is not None
        else None
    )
    return Report(
        id=r["id"],
        received_at=r["received_at"],
        reporter_id=r["reporter_id"],
        client_report_id=r["client_report_id"],
        product_id=r["product_id"],
        shop_id=r["shop_id"],
        area_id=r["area_id"],
        normalized=NormalizedPrice(
            product_id=r["product_id"],
            base_unit=_base_unit(r),
            base_quantity=r["base_quantity"],
            norm_price=r["norm_price"],
            raw=raw,
        ),
        observed_at=r["observed_at"],
        location=location,
        distance_to_shop_m=r["distance_to_shop_m"],
        location_verified=r["location_verified"],
        ip_hash=r["ip_hash"],
    )


_BASE_UNIT_OF = {dimension: base for base, dimension in BASE_DIMENSION.items()}


def _base_unit(r: dict[str, Any]) -> BaseUnit:
    """Not stored on the report: the base unit of the raw unit's dimension."""
    return _BASE_UNIT_OF[UNITS[parse_unit(r["raw_unit"])][0]]
