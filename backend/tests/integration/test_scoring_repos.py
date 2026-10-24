import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.postgres.tables import price_stats_daily, report_assessments
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.core.enums import AnomalyKind, BaseUnit, Classification, Direction, ScoringState
from spm.core.models import (
    Anomaly,
    Assessment,
    DailyStat,
    NormalizedPrice,
    RawPriceInput,
    Report,
    Reporter,
)

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC).replace(microsecond=0)
DAY = date(2026, 10, 10)


def _stat(shop: str | None, p50: float, *, final: bool = False) -> DailyStat:
    return DailyStat("onion", "demo-central", shop, DAY, p50 - 1, p50, p50 + 1, 0.5, 4, 2.0, final)


async def test_upsert_daily_survives_generic_plans_and_respects_final(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Regression: run the same prepared upsert > 5 times on ONE connection (Postgres switches to
    # a generic plan at the 6th execution; ON CONFLICT on the COALESCE index must still match).
    async with PgUnitOfWork(session_factory) as uow:
        for i in range(8):
            await uow.stats.upsert_daily([_stat(None, 80 + i), _stat("shop-a", 70 + i)])
        await uow.commit()
    async with PgUnitOfWork(session_factory) as uow:
        area = await uow.stats.series("onion", "demo-central", DAY, DAY)
        shop = await uow.stats.series("onion", "demo-central", DAY, DAY, shop_id="shop-a")
        assert [s.p50 for s in area] == [87.0] and [s.p50 for s in shop] == [77.0]

        await uow.stats.upsert_daily([_stat(None, 90, final=True)])
        await uow.stats.upsert_daily([_stat(None, 50)])  # live scoring can't rewrite a final day
        await uow.commit()
    async with session_factory() as s:
        rows = (
            await s.execute(
                select(price_stats_daily.c.p50, price_stats_daily.c.final).where(
                    price_stats_daily.c.shop_id.is_(None)
                )
            )
        ).all()
    assert [(float(p), f) for p, f in rows] == [(90.0, True)]


async def test_area_median_counts_included_labels_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with PgUnitOfWork(session_factory) as uow:
        reporter = Reporter(uuid.uuid4(), "dev", 1, 1, 0, False, NOW)
        await uow.reporters.create(reporter)
        for price in (78, 80, 82, 500):
            raw = RawPriceInput("onion", None, Decimal(price), Decimal(1), "kg")
            await uow.reports.add(
                Report(
                    uuid.uuid4(),
                    NOW,
                    reporter.id,
                    uuid.uuid4(),
                    "onion",
                    "shop-a",
                    "demo-central",
                    NormalizedPrice("onion", BaseUnit.KG, Decimal(1), Decimal(price), raw),
                    NOW - timedelta(hours=1),
                    None,
                    None,
                    True,
                    None,
                )
            )
        await uow.session.execute(update(report_assessments).values(classification="NORMAL"))
        await uow.session.execute(
            update(report_assessments)
            .where(report_assessments.c.norm_price == 500)
            .values(classification="BAD_REPORT")
        )
        await uow.commit()
    async with PgUnitOfWork(session_factory) as uow:
        assert await uow.stats.area_median("onion", "demo-central", NOW - timedelta(days=90)) == (
            80.0,
            3,
        )
        assert await uow.stats.area_median("rice", "demo-central", NOW - timedelta(days=90)) == (
            None,
            0,
        )

        obs = await uow.assessments.observations_for_area(
            "onion", "demo-central", NOW - timedelta(days=1)
        )
        target = next(o for o in obs if o.norm_price == 80)
        assert await uow.assessments.get_observation(target.report_id) == target
        await uow.assessments.save(
            Assessment(
                target.report_id,
                Classification.PENDING,
                Direction.UP,
                1.0,
                4.0,
                79.5,
                False,
                "test",
            ),
            NOW,
        )
        assert await uow.assessments.get_state(target.report_id) == (
            ScoringState.SCORED,
            Classification.PENDING,
        )
        pending = await uow.assessments.pending_for_shop("onion", "shop-a", NOW - timedelta(days=1))
        assert [p.report_id for p in pending] == [target.report_id]
        await uow.assessments.resolve(
            target.report_id, Classification.SHOP_OUTLIER, uuid.uuid4(), NOW
        )
        await uow.assessments.mark_trust_settled([target.report_id])
        unsettled = await uow.assessments.unsettled_for_shop(
            "onion", "shop-a", NOW - timedelta(days=1)
        )
        assert target.report_id not in {o.report_id for o in unsettled}
        await uow.commit()


async def test_open_anomaly_dedup_on_one_connection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def anomaly() -> Anomaly:
        return Anomaly(
            uuid.uuid4(),
            AnomalyKind.SHOP_OUTLIER,
            Direction.UP,
            "onion",
            "demo-central",
            "shop-c",
            120.0,
            80.0,
            50.0,
            (uuid.uuid4(),),
            NOW,
        )

    async with PgUnitOfWork(session_factory) as uow:
        opened = [await uow.anomalies.open_if_absent(anomaly(), "k") for _ in range(8)]
        await uow.commit()
        assert opened == [True] + [False] * 7
        recent = await uow.anomalies.recent("onion", "demo-central", NOW - timedelta(hours=1))
        assert len(recent) == 1 and recent[0].magnitude_pct == 50.0
        assert (
            await uow.session.execute(select(func.count()).select_from(price_stats_daily))
        ).scalar() == 0
