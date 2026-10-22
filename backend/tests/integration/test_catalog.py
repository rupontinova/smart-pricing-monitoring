import dataclasses

import pytest
import redis.asyncio as aioredis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.catalog_yaml import SeedCatalog, load_catalog
from spm.adapters.memory import MemoryCatalogReader, MemoryShopGeoIndex
from spm.adapters.postgres.catalog_repo import PgCatalogReader
from spm.adapters.postgres.catalog_seed import seed_catalog
from spm.adapters.postgres.tables import areas, product_aliases, products, shops
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.adapters.redis.keys import GEO_SHOPS
from spm.api.app import create_app
from spm.core.models import GeoPoint
from tests.conftest import client_for, make_settings
from tests.integration.conftest import TEST_REDIS_URL

pytestmark = pytest.mark.integration

CENTER = GeoPoint(23.7808, 90.4073)


async def _counts(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with session_factory() as session:
        return {
            t.name: (await session.execute(select(func.count()).select_from(t))).scalar_one()
            for t in (areas, products, product_aliases, shops)
        }


async def _half_life(
    session_factory: async_sessionmaker[AsyncSession], pid: str
) -> tuple[int, int]:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(products.c.half_life_h, products.c.default_half_life_h).where(
                    products.c.id == pid
                )
            )
        ).one()
    return row[0], row[1]


async def test_seed_twice_gives_same_row_counts(
    session_factory: async_sessionmaker[AsyncSession], redis_client: aioredis.Redis
) -> None:
    seed = load_catalog()
    first = await seed_catalog(seed, session_factory, RedisHotCache(redis_client))
    counts = await _counts(session_factory)
    second = await seed_catalog(seed, session_factory, RedisHotCache(redis_client))
    assert counts == {"areas": 1, "products": 10, "product_aliases": 22, "shops": 5}
    assert await _counts(session_factory) == counts
    assert first == second
    assert first.geo_shops == 5
    assert await redis_client.zcard(GEO_SHOPS) == 5


async def test_tuned_half_life_survives_reseed_untuned_follows_default(
    session_factory: async_sessionmaker[AsyncSession], redis_client: aioredis.Redis
) -> None:
    seed = load_catalog()
    geo = RedisHotCache(redis_client)
    await seed_catalog(seed, session_factory, geo)

    # M17 tunes onion's half-life.
    async with session_factory() as session:
        await session.execute(
            update(products).where(products.c.id == "onion").values(half_life_h=60)
        )
        await session.commit()

    # The seed's defaults change for onion and potato.
    changed = SeedCatalog(
        areas=seed.areas,
        products=tuple(
            dataclasses.replace(p, half_life_h=72) if p.id in ("onion", "potato") else p
            for p in seed.products
        ),
        shops=seed.shops,
    )
    await seed_catalog(changed, session_factory, geo)

    assert await _half_life(session_factory, "onion") == (60, 72)  # tuned value kept
    assert await _half_life(session_factory, "potato") == (72, 72)  # untuned: follows the default
    assert await _half_life(session_factory, "rice") == (336, 336)


async def test_pg_reader_matches_seed_files(
    session_factory: async_sessionmaker[AsyncSession], redis_client: aioredis.Redis
) -> None:
    await seed_catalog(load_catalog(), session_factory, RedisHotCache(redis_client))
    pg, mem = PgCatalogReader(session_factory), MemoryCatalogReader.from_seeds()

    assert await pg.products() == await mem.products()
    assert await pg.alias_map() == await mem.alias_map()
    assert await pg.areas() == await mem.areas()
    assert await pg.shops() == await mem.shops()
    assert await pg.shops("demo-central") == await mem.shops("demo-central")
    assert await pg.product("eggs") == await mem.product("eggs")
    assert await pg.product("nope") is None
    assert await pg.shop("shop-c") == await mem.shop("shop-c")
    assert await pg.shop("nope") is None


async def test_redis_geo_matches_memory_geo(redis_client: aioredis.Redis) -> None:
    shop_list = list(load_catalog().shops)
    redis_geo, mem_geo = RedisHotCache(redis_client), MemoryShopGeoIndex()
    await redis_geo.load_shop_geo(shop_list)
    await mem_geo.load_shop_geo(shop_list)
    for radius in (250, 1000, 1600):
        got = await redis_geo.shops_within(CENTER, radius)
        want = await mem_geo.shops_within(CENTER, radius)
        assert [s for s, _ in got] == [s for s, _ in want]
        assert [d for _, d in got] == pytest.approx([d for _, d in want], abs=1.0)


async def test_load_shop_geo_replaces_previous_members(redis_client: aioredis.Redis) -> None:
    shop_list = list(load_catalog().shops)
    geo = RedisHotCache(redis_client)
    await geo.load_shop_geo(shop_list)
    await geo.load_shop_geo([dataclasses.replace(shop_list[0], active=False), *shop_list[1:3]])
    assert sorted(await redis_client.zrange(GEO_SHOPS, 0, -1)) == [b"shop-b", b"shop-c"]


async def test_get_shops_within_1km_sorted_ascending(
    database_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: aioredis.Redis,
) -> None:
    await seed_catalog(load_catalog(), session_factory, RedisHotCache(redis_client))
    await redis_client.delete(GEO_SHOPS)  # the app's startup warm-up must rebuild it

    app = create_app(make_settings(database_url=database_url, redis_url=TEST_REDIS_URL))
    async for client in client_for(app):
        response = await client.get(
            "/v1/shops", params={"lat": CENTER.lat, "lng": CENTER.lng, "radius_m": 1000}
        )
        products_response = await client.get("/v1/products")
    assert response.status_code == 200, response.text
    shops_out = response.json()
    assert [s["id"] for s in shops_out] == ["shop-a", "shop-b", "shop-c"]
    distances = [s["distance_m"] for s in shops_out]
    assert distances == sorted(distances)
    assert all(d <= 1000 for d in distances)
    assert len(products_response.json()) == 10


async def test_pg_reader_returns_empty_catalog_before_seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert (await session.execute(text("SELECT count(*) FROM products"))).scalar() == 0
    assert await PgCatalogReader(session_factory).products() == []
