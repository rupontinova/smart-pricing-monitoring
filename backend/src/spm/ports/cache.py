from typing import Protocol

from spm.core.models import FairBand, GeoPoint, Shop, ShopPrice


class ShopGeoIndex(Protocol):
    """Shop locations for "nearby" queries (Redis GEO ``spm:geo:shops``)."""

    async def shops_within(self, point: GeoPoint, radius_m: float) -> list[tuple[str, float]]:
        """(shop_id, distance_m), nearest first."""
        ...

    async def load_shop_geo(self, shops: list[Shop]) -> None:
        """Replace the index with these shops."""
        ...


class HotCache(ShopGeoIndex, Protocol):
    async def get_band(self, area_id: str, product_id: str) -> FairBand | None: ...
    async def put_band(self, band: FairBand, ttl_s: int) -> None: ...
    async def put_shop_prices(
        self, area_id: str, product_id: str, prices: list[ShopPrice], ttl_s: int
    ) -> None: ...
    async def cheapest(
        self, area_id: str, product_id: str, max_price: float, limit: int
    ) -> list[ShopPrice]: ...
    async def shop_price(self, area_id: str, product_id: str, shop_id: str) -> ShopPrice | None: ...


class RateLimiter(Protocol):
    async def hit(self, scope: str, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        """Count one hit; return (allowed, retry_after_s). retry_after_s is 0 when allowed."""
        ...
