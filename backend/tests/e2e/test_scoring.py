import asyncio
import statistics
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from spm.adapters.postgres.tables import (
    anomalies,
    band_state,
    price_stats_daily,
    report_assessments,
    reporters,
)
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.constants import MARKET_SHIFT_WINDOW_H
from spm.core.enums import AnomalyKind, Classification, Direction, ScoringState
from spm.core.events import PriceReported, make_event
from tests.e2e.conftest import World

pytestmark = pytest.mark.e2e
C = Classification


async def _count(world: World, table: object) -> int:
    async with world.sf() as s:
        return int((await s.execute(select(func.count()).select_from(table))).scalar_one())  # type: ignore[arg-type]


async def _band_p50(world: World) -> float | None:
    async with PgUnitOfWork(world.sf) as uow:
        band = await uow.bands.get_band("onion", "demo-central")
    return band.p50 if band else None


async def test_ten_normal_reports_score_band_and_events(world: World) -> None:
    world.subscribe()
    ids = []
    for i in range(10):
        ids.append(await world.submit(f"shop-{'abcde'[i % 5]}", 79 + i % 3))
        world.clock.advance(minutes=10)

    for rid in ids:
        assert await world.state(rid) == (ScoringState.SCORED, C.NORMAL)
    p50 = await _band_p50(world)
    assert p50 is not None and p50 == pytest.approx(80, abs=1)
    cached = await world.hot.get_band("demo-central", "onion")
    assert cached is not None and cached.p50 == pytest.approx(p50, abs=1e-4)
    assert world.counts()["band.updated"] == 10
    assert world.counts()["price.reported"] == 10
    assert await _count(world, price_stats_daily) >= 2  # area row + shop rows

    timings = [c.value for c in world.metrics.calls if c.name == "scoring_ms"]
    print(f"\nscoring_ms: median {statistics.median(timings):.1f}, max {max(timings):.1f}")


async def test_pending_promoted_to_shop_outlier_opens_one_anomaly(world: World) -> None:
    world.subscribe()
    await world.baseline()

    spike = await world.submit("shop-c", 120)
    assert (await world.state(spike))[1] is C.PENDING

    world.clock.advance(hours=2)
    confirm = await world.submit("shop-c", 119)
    assert (await world.state(confirm))[1] is C.SHOP_OUTLIER
    assert (await world.state(spike))[1] is C.SHOP_OUTLIER  # promoted

    world.clock.advance(hours=1)
    third = await world.submit("shop-c", 121)
    assert (await world.state(third))[1] is C.SHOP_OUTLIER

    async with world.sf() as s:
        rows = (
            await s.execute(select(anomalies.c.kind, anomalies.c.shop_id, anomalies.c.direction))
        ).all()
    assert rows == [("SHOP_OUTLIER", "shop-c", "up")]
    assert world.counts()["anomaly.detected"] == 1
    detected = next(e for e in world.events if e.type == "anomaly.detected")
    assert detected.data["magnitude_pct"] > 30


async def test_market_shock_one_shift_rebase_and_band_moves(world: World) -> None:
    world.subscribe()
    await world.baseline(days=2, start_days_ago=4)  # baseline ends 2 days before T0

    first = [await world.submit(shop, 110) for shop in ("shop-a", "shop-b")]
    for rid in first:
        assert (await world.state(rid))[1] is C.PENDING
    shift = await world.submit("shop-c", 111)
    assert (await world.state(shift))[1] is C.MARKET_SHIFT

    async with PgUnitOfWork(world.sf) as uow:
        rebase = await uow.bands.get_rebase("onion", "demo-central")
        recent = await uow.anomalies.recent(
            "onion", "demo-central", world.clock.now() - timedelta(days=1)
        )
    assert rebase == world.clock.now() - timedelta(hours=MARKET_SHIFT_WINDOW_H)
    assert [(a.kind, a.direction, a.shop_id) for a in recent] == [
        (AnomalyKind.MARKET_SHIFT, Direction.UP, None)
    ]

    world.clock.advance(minutes=30)
    nxt = await world.submit("shop-d", 112)
    assert (await world.state(nxt))[1] is C.MARKET_SHIFT
    assert world.counts()["anomaly.detected"] == 1  # deduplicated
    p50 = await _band_p50(world)
    assert p50 is not None and p50 > 105  # the band moved to the new level


async def test_contradicted_pending_becomes_bad_report_and_beta_rises(world: World) -> None:
    world.subscribe()
    await world.baseline()

    liar = "liar-device"
    bad = await world.submit("shop-c", 160, device=liar)
    assert (await world.state(bad))[1] is C.PENDING

    world.clock.advance(hours=1)
    honest = await world.submit("shop-c", 81)
    assert (await world.state(honest))[1] is C.NORMAL
    assert (await world.state(bad))[1] is C.BAD_REPORT

    async with world.sf() as s:
        alpha, beta = (
            await s.execute(
                select(reporters.c.alpha, reporters.c.beta).where(reporters.c.device_id == liar)
            )
        ).one()
        resolved_by = (
            await s.execute(
                select(report_assessments.c.resolved_by).where(
                    report_assessments.c.report_id == bad
                )
            )
        ).scalar_one()
    assert (alpha, beta) == (1.0, 2.0)
    assert resolved_by == honest


async def test_same_event_delivered_twice_is_processed_once(world: World) -> None:
    world.subscribe(scoring=False)
    await world.submit("shop-a", 80)
    (reported,) = [e for e in world.events if e.type == "price.reported"]

    await world.scoring.handler(reported)
    counts = {t: await _count(world, t) for t in (anomalies, band_state, price_stats_daily)}
    published = len(world.bus.published)

    await world.scoring.handler(reported)  # redelivery
    assert {t: await _count(world, t) for t in counts} == counts
    assert len(world.bus.published) == published  # no second set of events
    assert world.metrics.total("reports_scored") == 1


async def test_concurrent_scoring_has_no_lost_band_updates(world: World) -> None:
    world.subscribe(scoring=False)  # collect price.reported events, score them all at once
    for i in range(20):
        await world.submit(f"shop-{'abcde'[i % 5]}", 76 + i % 9)
    reported = [e for e in world.events if e.type == "price.reported"]
    assert len(reported) == 20

    await asyncio.gather(*(world.scoring.handler(e) for e in reported))

    async with PgUnitOfWork(world.sf) as uow:
        assert (
            await uow.session.execute(
                select(func.count()).where(report_assessments.c.scoring_state == "scored")
            )
        ).scalar_one() == 20
        stored = await uow.bands.get_band("onion", "demo-central")
        catalog = await world.catalog.ensure_fresh()
        product = catalog.product("onion")
        assert product is not None and stored is not None
        fresh, _ = await world.scoring_band_service().recompute(
            uow, product, "demo-central", stored.computed_at
        )
        await uow.rollback()
    assert stored.p50 == pytest.approx(fresh.p50, abs=1e-4)
    assert stored.total_weight == pytest.approx(fresh.total_weight, rel=1e-5)
    cached = await world.hot.get_band("demo-central", "onion")
    assert cached is not None and cached.computed_at == stored.computed_at  # newest band cached


async def test_bad_data_marks_assessment_failed_without_retry(world: World) -> None:
    world.subscribe(scoring=False)
    rid = await world.submit("shop-a", 80)
    (reported,) = [e for e in world.events if e.type == "price.reported"]
    data = {**reported.data, "product_id": "ghost"}  # not in the catalog
    poisoned = make_event(
        "price.reported",
        PriceReported.model_validate(data),
        "test",
        None,
        world.clock,
        world.container.ids,
    )
    await world.scoring.handler(poisoned)  # returns normally
    assert (await world.state(rid))[0] is ScoringState.FAILED
