import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spm.adapters.memory import FixedClock, MemoryHotCache, MemoryUnitOfWork
from spm.core.enums import BaseUnit, Classification, Confidence, ScoringState
from spm.core.models import NormalizedPrice, Product, RawPriceInput, Report, Reporter, ShopPrice
from spm.services.band_service import BandService

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
ONION = Product("onion", "Onion", BaseUnit.KG, "vegetable", 48, Decimal(10), Decimal(500))


def _report(uow: MemoryUnitOfWork, shop: str, price: float, hours_ago: float) -> Report:
    reporter = Reporter(uuid.uuid4(), str(uuid.uuid4()), 3.0, 1.0, 0, False, NOW)
    uow.reporters.by_id[reporter.id] = reporter
    raw = RawPriceInput("onion", None, Decimal(str(price)), Decimal(1), "kg")
    report = Report(
        uuid.uuid4(), NOW, reporter.id, uuid.uuid4(), "onion", shop, "demo-central",
        NormalizedPrice("onion", BaseUnit.KG, Decimal(1), Decimal(str(price)), raw),
        NOW - timedelta(hours=hours_ago), None, None, True, None,
    )  # fmt: skip
    uow.reports.by_id[report.id] = report
    uow.reports.states[report.id] = (ScoringState.SCORED, Classification.NORMAL)
    return report


@pytest.fixture
def setup() -> tuple[BandService, MemoryUnitOfWork, MemoryHotCache, FixedClock]:
    clock = FixedClock(NOW)
    uow, hot = MemoryUnitOfWork(), MemoryHotCache(clock)
    for shop in ("shop-a", "shop-b", "shop-c"):
        _report(uow, shop, 80, 1)
        _report(uow, shop, 81, 3)
    return BandService(hot, lambda: uow, clock), uow, hot, clock


async def test_recompute_persists_band_and_shop_prices(setup) -> None:  # type: ignore[no-untyped-def]
    service, uow, _, _ = setup
    band, prices = await service.recompute(uow, ONION, "demo-central", NOW)
    assert band.p50 is not None and 80 <= band.p50 <= 81
    assert band.confidence is Confidence.HIGH  # 6 reports × 0.75 trust, 3 shops
    assert [sp.shop_id for sp in prices] == ["shop-a", "shop-b", "shop-c"]
    assert all(sp.verified for sp in prices)
    assert await uow.bands.get_band("onion", "demo-central") == band
    assert await uow.bands.shop_prices("onion", "demo-central") == prices


async def test_recompute_respects_rebase_and_drops_stale_shops(setup) -> None:  # type: ignore[no-untyped-def]
    service, uow, _, _ = setup
    await uow.bands.upsert_shop_price(
        ShopPrice(
            "onion", "shop-z", "demo-central", 70.0, True, 1.0, 2, NOW - timedelta(days=9), None
        )
    )
    await uow.bands.set_rebase("onion", "demo-central", NOW - timedelta(hours=2))
    band, prices = await service.recompute(uow, ONION, "demo-central", NOW)
    assert band.p50 == 80  # only the 1-hour-old reports are after the re-base
    assert band.rebase_at == NOW - timedelta(hours=2)
    assert len(prices) == 3  # shop prices ignore the re-base
    assert "shop-z" not in {
        sp.shop_id for sp in await uow.bands.shop_prices("onion", "demo-central")
    }


async def test_publish_and_get_or_rebuild(setup) -> None:  # type: ignore[no-untyped-def]
    service, uow, hot, clock = setup
    band, prices = await service.recompute(uow, ONION, "demo-central", NOW)
    await service.publish_to_cache(band, prices)
    assert hot.ttls == {"band": 96 * 3600, "prices": 96 * 3600}
    assert await service.get_or_rebuild("demo-central", "onion") == band

    hot.flush()
    rebuilt = await service.get_or_rebuild("demo-central", "onion")
    assert rebuilt == band
    assert [sp.shop_id for sp in await hot.cheapest("demo-central", "onion", 1000, 5)] == [
        "shop-a", "shop-b", "shop-c",
    ]  # fmt: skip

    clock.advance(hours=4 * 24 + 1)  # past the TTL and past 2 half-lives
    assert await hot.get_band("demo-central", "onion") is None
    await service.get_or_rebuild("demo-central", "onion")
    assert await hot.cheapest("demo-central", "onion", 1000, 5) == []  # stale prices not cached


async def test_get_or_rebuild_unknown_band(setup) -> None:  # type: ignore[no-untyped-def]
    service, *_ = setup
    assert await service.get_or_rebuild("demo-central", "garlic") is None


async def test_memory_hot_cache_cheapest_and_shop_price() -> None:
    hot = MemoryHotCache()
    t = NOW
    prices = [
        ShopPrice("onion", "s1", "a", 70.0, True, 1, 2, t, t),
        ShopPrice("onion", "s2", "a", 65.0, False, 1, 1, t, None),
        ShopPrice("onion", "s3", "a", 75.0, True, 1, 2, t, t),
        ShopPrice("onion", "s4", "a", 90.0, True, 1, 2, t, t),
    ]
    await hot.put_shop_prices("a", "onion", prices, 3600)
    assert [sp.shop_id for sp in await hot.cheapest("a", "onion", 80, 5)] == ["s1", "s3"]
    assert [sp.shop_id for sp in await hot.cheapest("a", "onion", 80, 1)] == ["s1"]
    assert await hot.cheapest("a", "onion", 80, 0) == []
    assert (await hot.shop_price("a", "onion", "s2")).verified is False  # type: ignore[union-attr]
    assert await hot.shop_price("a", "onion", "nope") is None
