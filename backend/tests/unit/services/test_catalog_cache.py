import asyncio
from datetime import UTC, datetime

import pytest

from spm.adapters.memory import FixedClock, MemoryCatalogReader
from spm.services.catalog_cache import CatalogCache

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)


@pytest.fixture
def reader() -> MemoryCatalogReader:
    return MemoryCatalogReader.from_seeds()


async def test_cache_refreshes_after_300s_and_not_before(reader: MemoryCatalogReader) -> None:
    clock = FixedClock(T0)
    cache = CatalogCache(reader, clock)
    await cache.ensure_fresh()
    assert reader.loads == 1

    clock.advance(seconds=299)
    await cache.ensure_fresh()
    assert reader.loads == 1

    clock.advance(seconds=1)
    await cache.ensure_fresh()
    assert reader.loads == 2
    assert cache.loaded_at == clock.now()


async def test_cache_sync_getters(reader: MemoryCatalogReader) -> None:
    cache = await CatalogCache(reader, FixedClock(T0)).ensure_fresh()
    product = cache.product("onion")
    assert product is not None and product.name == "Onion"
    assert cache.product("nope") is None
    assert len(cache.products()) == 10
    assert cache.alias_map()["piyaz"] == "onion"
    assert [s.id for s in cache.shops_in_area("demo-central")][:2] == ["shop-a", "shop-b"]
    assert cache.shops_in_area("elsewhere") == []
    assert cache.shop("shop-c") is not None
    area = cache.area("demo-central")
    assert area is not None and area.timezone == "Asia/Dhaka"


def test_cache_getter_before_load_raises(reader: MemoryCatalogReader) -> None:
    with pytest.raises(RuntimeError, match="ensure_fresh"):
        CatalogCache(reader, FixedClock(T0)).product("onion")


async def test_concurrent_callers_load_once(reader: MemoryCatalogReader) -> None:
    cache = CatalogCache(reader, FixedClock(T0))
    await asyncio.gather(*(cache.ensure_fresh() for _ in range(20)))
    assert reader.loads == 1


class _FlakyReader(MemoryCatalogReader):
    fail = False

    async def products(self):  # type: ignore[no-untyped-def]
        if self.fail:
            raise ConnectionError("db down")
        return await super().products()


async def test_stale_snapshot_served_when_refresh_fails() -> None:
    reader = _FlakyReader(MemoryCatalogReader.from_seeds().catalog)
    clock = FixedClock(T0)
    cache = await CatalogCache(reader, clock).ensure_fresh()
    reader.fail = True
    clock.advance(seconds=600)
    await cache.ensure_fresh()  # doesn't raise
    assert cache.product("onion") is not None
    assert cache.loaded_at == T0


async def test_first_load_failure_propagates() -> None:
    reader = _FlakyReader(MemoryCatalogReader.from_seeds().catalog)
    reader.fail = True
    with pytest.raises(ConnectionError):
        await CatalogCache(reader, FixedClock(T0)).ensure_fresh()
