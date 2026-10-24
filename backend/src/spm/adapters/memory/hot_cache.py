"""``MemoryHotCache``: the hot cache in a dict (tests, simulator). With a clock, TTLs apply."""

from datetime import datetime, timedelta

from spm.adapters.memory.geo import MemoryShopGeoIndex
from spm.core.models import FairBand, GeoPoint, Shop, ShopPrice
from spm.ports import Clock


class MemoryHotCache:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock
        self.bands: dict[tuple[str, str], tuple[FairBand, datetime | None]] = {}
        self.prices: dict[tuple[str, str], tuple[list[ShopPrice], datetime | None]] = {}
        self.ttls: dict[str, int] = {}  # last TTL used per key kind, for assertions
        self._geo = MemoryShopGeoIndex()

    def _expiry(self, ttl_s: int) -> datetime | None:
        return self._clock.now() + timedelta(seconds=ttl_s) if self._clock else None

    def _alive(self, expires: datetime | None) -> bool:
        return expires is None or self._clock is None or self._clock.now() < expires

    def flush(self) -> None:
        self.bands.clear()
        self.prices.clear()

    async def put_band(self, band: FairBand, ttl_s: int) -> None:
        self.bands[(band.area_id, band.product_id)] = (band, self._expiry(ttl_s))
        self.ttls["band"] = ttl_s

    async def get_band(self, area_id: str, product_id: str) -> FairBand | None:
        entry = self.bands.get((area_id, product_id))
        return entry[0] if entry and self._alive(entry[1]) else None

    async def put_shop_prices(
        self, area_id: str, product_id: str, prices: list[ShopPrice], ttl_s: int
    ) -> None:
        self.prices[(area_id, product_id)] = (
            sorted(prices, key=lambda sp: sp.price),
            self._expiry(ttl_s),
        )
        self.ttls["prices"] = ttl_s

    def _prices(self, area_id: str, product_id: str) -> list[ShopPrice]:
        entry = self.prices.get((area_id, product_id))
        return entry[0] if entry and self._alive(entry[1]) else []

    async def cheapest(
        self, area_id: str, product_id: str, max_price: float, limit: int
    ) -> list[ShopPrice]:
        below = [sp for sp in self._prices(area_id, product_id) if sp.price < max_price]
        return [sp for sp in below[: limit * 3] if sp.verified][: max(limit, 0)]

    async def shop_price(self, area_id: str, product_id: str, shop_id: str) -> ShopPrice | None:
        return next((sp for sp in self._prices(area_id, product_id) if sp.shop_id == shop_id), None)

    async def put_snapshot(self, band: FairBand, prices: list[ShopPrice], ttl_s: int) -> bool:
        current = await self.get_band(band.area_id, band.product_id)
        if current is not None and current.computed_at > band.computed_at:
            return False
        await self.put_band(band, ttl_s)
        await self.put_shop_prices(band.area_id, band.product_id, prices, ttl_s)
        return True

    async def load_shop_geo(self, shops: list[Shop]) -> None:
        await self._geo.load_shop_geo(shops)

    async def shops_within(self, point: GeoPoint, radius_m: float) -> list[tuple[str, float]]:
        return await self._geo.shops_within(point, radius_m)
