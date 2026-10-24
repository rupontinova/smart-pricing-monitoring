import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import redis.asyncio as aioredis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.catalog_yaml import load_catalog
from spm.adapters.memory import FixedClock
from spm.adapters.postgres.catalog_seed import seed_catalog
from spm.adapters.postgres.tables import report_assessments
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.adapters.redis.keys import band_key, cheap_key, shop_price_key
from spm.core.enums import BaseUnit, Classification, Confidence
from spm.core.models import (
    FairBand,
    NormalizedPrice,
    RawPriceInput,
    Report,
    Reporter,
    ShopPrice,
)
from spm.services.band_service import BandService
from tests.integration.conftest import TEST_REDIS_URL

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
async def redis(redis_client: aioredis.Redis) -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


def _band(**kw: object) -> FairBand:
    values: dict[str, object] = dict(
        product_id="onion",
        area_id="demo-central",
        p25=78.5,
        p50=80.0,
        p75=81.25,
        mad=1.1,
        total_weight=4.2,
        n_eff=3.7,
        n_shops=4,
        confidence=Confidence.HIGH,
        newest_obs_at=NOW - timedelta(hours=1),
        mean_age_h=6.5,
        half_life_h=48,
        rebase_at=None,
        computed_at=NOW,
    )
    values.update(kw)
    return FairBand(**values)  # type: ignore[arg-type]


def _price(shop: str, price: float, verified: bool) -> ShopPrice:
    return ShopPrice(
        "onion",
        shop,
        "demo-central",
        price,
        verified,
        1.5,
        2,
        NOW - timedelta(hours=1),
        NOW if verified else None,
    )


# ------------------------------------------------------------------------------ Redis
async def test_band_round_trip_and_ttl(redis: aioredis.Redis) -> None:
    hot = RedisHotCache(redis)
    band = _band(rebase_at=NOW - timedelta(days=1))
    await hot.put_band(band, 3600)
    assert await hot.get_band("demo-central", "onion") == band
    assert 0 < await redis.ttl(band_key("demo-central", "onion")) <= 3600

    empty = _band(
        p25=None,
        p50=None,
        p75=None,
        mad=None,
        confidence=Confidence.STALE,
        newest_obs_at=None,
        mean_age_h=None,
        total_weight=0.0,
        n_eff=0.0,
        n_shops=0,
    )
    await hot.put_band(empty, 3600)
    assert await hot.get_band("demo-central", "onion") == empty
    assert await hot.get_band("demo-central", "garlic") is None


async def test_cheapest_only_verified_below_max_sorted(redis: aioredis.Redis) -> None:
    hot = RedisHotCache(redis)
    prices = [
        _price("shop-a", 82.0, True),
        _price("shop-b", 76.0, False),  # cheapest, but unverified
        _price("shop-c", 78.0, True),
        _price("shop-d", 79.0, True),
        _price("shop-e", 80.0, True),  # not strictly below 80
    ]
    await hot.put_shop_prices("demo-central", "onion", prices, 7200)
    cheapest = await hot.cheapest("demo-central", "onion", 80.0, 3)
    assert [(sp.shop_id, sp.price) for sp in cheapest] == [("shop-c", 78.0), ("shop-d", 79.0)]
    assert [sp.shop_id for sp in await hot.cheapest("demo-central", "onion", 100.0, 1)] == [
        "shop-c"
    ]
    assert await hot.shop_price("demo-central", "onion", "shop-b") == prices[1]
    assert await hot.shop_price("demo-central", "onion", "shop-z") is None
    for key in (cheap_key("demo-central", "onion"), shop_price_key("demo-central", "onion")):
        assert 0 < await redis.ttl(key) <= 7200

    await hot.put_shop_prices("demo-central", "onion", [_price("shop-a", 82.0, True)], 7200)
    assert await redis.zcard(cheap_key("demo-central", "onion")) == 1  # replaced, not merged
    await hot.put_shop_prices("demo-central", "onion", [], 7200)
    assert await redis.exists(cheap_key("demo-central", "onion")) == 0


# --------------------------------------------------------------------------- Postgres
async def _seed_reports(
    sf: async_sessionmaker[AsyncSession], redis: aioredis.Redis
) -> list[Report]:
    await seed_catalog(load_catalog(), sf, RedisHotCache(redis))
    reports = []
    async with PgUnitOfWork(sf) as uow:
        for i, (shop, price, hours) in enumerate(
            [
                ("shop-a", 80, 1),
                ("shop-a", 81, 3),
                ("shop-b", 79, 2),
                ("shop-b", 80, 4),
                ("shop-c", 82, 1),
                ("shop-c", 81, 5),
            ]
        ):
            reporter = Reporter(
                uuid.uuid4(), f"dev-{i}", 19.0 if i == 0 else 3.0, 1.0, 0, False, NOW
            )
            await uow.reporters.create(reporter)
            raw = RawPriceInput("onion", None, Decimal(price), Decimal(1), "kg")
            report = Report(
                uuid.uuid4(),
                NOW,
                reporter.id,
                uuid.uuid4(),
                "onion",
                shop,
                "demo-central",
                NormalizedPrice("onion", BaseUnit.KG, Decimal(1), Decimal(price), raw),
                NOW - timedelta(hours=hours),
                None,
                None,
                True,
                None,
            )
            await uow.reports.add(report)
            reports.append(report)
        await uow.session.execute(
            update(report_assessments).values(scoring_state="scored", classification="NORMAL")
        )
        await uow.commit()
    return reports


async def test_pg_observations_and_high_trust(
    session_factory: async_sessionmaker[AsyncSession], redis: aioredis.Redis
) -> None:
    reports = await _seed_reports(session_factory, redis)
    async with PgUnitOfWork(session_factory) as uow:
        obs = await uow.assessments.observations_for_area(
            "onion", "demo-central", NOW - timedelta(hours=3, minutes=30)
        )
        assert [o.norm_price for o in obs] == [80.0, 82.0, 79.0, 81.0]  # newest first
        assert all(o.classification is Classification.NORMAL for o in obs)
        assert obs[0].trust == pytest.approx(19 / 20)
        others = await uow.assessments.observations_for_area(
            "onion", "demo-central", NOW - timedelta(days=1), exclude_shop="shop-a"
        )
        assert {o.shop_id for o in others} == {"shop-b", "shop-c"}
        shop_c = await uow.assessments.observations_for_shop(
            "onion", "shop-c", NOW - timedelta(days=1)
        )
        assert len(shop_c) == 2
        high = await uow.reporters.high_trust_ids([r.reporter_id for r in reports])
        assert high == {reports[0].reporter_id}  # 19/20 = 0.95, evidence 18


async def test_pg_band_repo_round_trip(
    session_factory: async_sessionmaker[AsyncSession], redis: aioredis.Redis
) -> None:
    await seed_catalog(load_catalog(), session_factory, RedisHotCache(redis))
    band = _band(p50=80.12345)  # stored as numeric(14,4)
    async with PgUnitOfWork(session_factory) as uow:
        assert await uow.bands.get_rebase("onion", "demo-central") is None
        await uow.bands.set_rebase("onion", "demo-central", NOW - timedelta(hours=5))  # creates row
        assert await uow.bands.get_rebase("onion", "demo-central") == NOW - timedelta(hours=5)
        await uow.bands.upsert_band(band)
        await uow.bands.replace_shop_prices(
            "onion", "demo-central", [_price("shop-a", 80.0, True), _price("shop-b", 81.0, False)]
        )
        await uow.commit()
    async with PgUnitOfWork(session_factory) as uow:
        stored = await uow.bands.get_band("onion", "demo-central")
        assert stored is not None and stored.p50 == 80.1234  # tie at 4 dp: half-even -> 4
        assert stored.confidence is Confidence.HIGH and stored.rebase_at is None
        await uow.bands.set_rebase("onion", "demo-central", NOW)
        await uow.bands.replace_shop_prices("onion", "demo-central", [_price("shop-c", 77.0, True)])
        await uow.commit()
    async with PgUnitOfWork(session_factory) as uow:
        assert await uow.bands.get_rebase("onion", "demo-central") == NOW
        assert [sp.shop_id for sp in await uow.bands.shop_prices("onion", "demo-central")] == [
            "shop-c"
        ]


async def test_get_or_rebuild_repopulates_redis_after_flushdb(
    session_factory: async_sessionmaker[AsyncSession], redis: aioredis.Redis
) -> None:
    await _seed_reports(session_factory, redis)
    hot = RedisHotCache(redis)
    service = BandService(hot, lambda: PgUnitOfWork(session_factory), FixedClock(NOW))
    product = load_catalog().products[0]
    assert product.id == "onion"
    async with PgUnitOfWork(session_factory) as uow:
        band, prices = await service.recompute(uow, product, "demo-central", NOW)
        await uow.commit()
    await service.publish_to_cache(band, prices)
    assert band.confidence is Confidence.HIGH and len(prices) == 3

    await redis.flushdb()
    assert await hot.get_band("demo-central", "onion") is None

    rebuilt = await service.get_or_rebuild("demo-central", "onion")
    assert rebuilt is not None and rebuilt.p50 == pytest.approx(band.p50, abs=1e-4)
    assert await hot.get_band("demo-central", "onion") == rebuilt
    assert await redis.zcard(cheap_key("demo-central", "onion")) == 3
    assert len(await hot.cheapest("demo-central", "onion", 1000, 5)) == sum(
        sp.verified for sp in prices
    )
