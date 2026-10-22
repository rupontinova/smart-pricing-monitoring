from spm.core.geo import haversine_m
from spm.core.models import GeoPoint, Shop


class MemoryShopGeoIndex:
    """``ShopGeoIndex`` using haversine distances (Redis GEO uses the same formula)."""

    def __init__(self) -> None:
        self._shops: dict[str, GeoPoint] = {}

    async def load_shop_geo(self, shops: list[Shop]) -> None:
        self._shops = {s.id: s.location for s in shops if s.active}

    async def shops_within(self, point: GeoPoint, radius_m: float) -> list[tuple[str, float]]:
        hits = [(shop_id, haversine_m(point, loc)) for shop_id, loc in self._shops.items()]
        return sorted((h for h in hits if h[1] <= radius_m), key=lambda h: h[1])
