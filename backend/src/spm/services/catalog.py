"""Read-only catalog queries behind the ``/v1`` catalog endpoints."""

from dataclasses import dataclass

from spm.core.errors import NotFound, ServiceUnavailable
from spm.core.models import Area, GeoPoint, Product, Shop
from spm.ports import ShopGeoIndex
from spm.services.catalog_cache import CatalogCache


@dataclass(frozen=True, slots=True)
class ShopHit:
    shop: Shop
    distance_m: float | None


class CatalogService:
    def __init__(self, cache: CatalogCache, geo: ShopGeoIndex | None) -> None:
        self._cache = cache
        self._geo = geo

    def products(self) -> list[Product]:
        return [p for p in self._cache.products() if p.active]

    def areas(self) -> list[Area]:
        return self._cache.areas()

    async def shops(
        self, area_id: str | None, near: GeoPoint | None, radius_m: float
    ) -> list[ShopHit]:
        """Active shops; nearest first within ``radius_m`` when ``near`` is given, else by name."""
        if area_id is not None and self._cache.area(area_id) is None:
            raise NotFound(f"Unknown area {area_id!r}.", area_id=area_id)

        def wanted(shop: Shop | None) -> bool:
            return shop is not None and shop.active and area_id in (None, shop.area_id)

        if near is None:
            found = [s for s in self._cache.shops() if wanted(s)]
            return [ShopHit(s, None) for s in sorted(found, key=lambda s: (s.name, s.id))]

        if self._geo is None:
            raise ServiceUnavailable("Nearby shop search is not available.")
        hits = []
        for shop_id, distance in await self._geo.shops_within(near, radius_m):
            shop = self._cache.shop(shop_id)
            if shop is not None and wanted(shop):
                hits.append(ShopHit(shop, distance))
        return hits
