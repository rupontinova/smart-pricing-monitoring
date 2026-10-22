"""``RedisHotCache``. M04 implements the shop geo index only; bands and prices arrive in M12."""

from typing import Any, cast

from redis.asyncio import Redis

from spm.adapters.redis.keys import GEO_SHOPS
from spm.core.models import GeoPoint, Shop


class RedisHotCache:
    def __init__(self, redis: "Redis") -> None:
        self._redis = redis

    async def load_shop_geo(self, shops: list[Shop]) -> None:
        """Atomically replace ``spm:geo:shops`` with the given shops (active ones only)."""
        members: list[float | str] = []
        for shop in shops:
            if shop.active:
                members += [shop.location.lng, shop.location.lat, shop.id]
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(GEO_SHOPS)
            if members:
                pipe.geoadd(GEO_SHOPS, members)
            await pipe.execute()

    async def shops_within(self, point: GeoPoint, radius_m: float) -> list[tuple[str, float]]:
        rows = await self._redis.geosearch(
            GEO_SHOPS,
            longitude=point.lng,
            latitude=point.lat,
            radius=radius_m,
            unit="m",
            sort="ASC",
            withdist=True,
        )
        # WITHDIST rows are [member, distance]; member is str with decode_responses, else bytes.
        return [(_text(row[0]), float(row[1])) for row in cast(list[list[Any]], rows)]


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value
