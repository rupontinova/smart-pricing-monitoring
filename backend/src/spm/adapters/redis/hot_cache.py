"""``RedisHotCache``: the read path's O(1) layer (MODULES.md §3.2).

- ``spm:band:{area}:{product}`` HASH: the fair band
- ``spm:cheap:{area}:{product}`` ZSET: shop_id scored by price per base unit
- ``spm:shopprice:{area}:{product}`` HASH: shop_id -> JSON price details
- ``spm:geo:shops`` GEO: shop locations (M04)
"""

from typing import Any, cast

from redis.asyncio import Redis

from spm.adapters.cache_codec import (
    decode_band,
    decode_shop_price,
    encode_band,
    encode_shop_price,
)
from spm.adapters.redis.keys import GEO_SHOPS, band_key, cheap_key, shop_price_key
from spm.core.models import FairBand, GeoPoint, Shop, ShopPrice

# KEYS: band, cheap, shopprice. ARGV: ttl, computed_ts, n_band_fields, (field, value)...,
#        n_shops, (shop_id, price, json)...
_PUT_SNAPSHOT = """
local current = redis.call('HGET', KEYS[1], 'computed_ts')
if current and tonumber(current) > tonumber(ARGV[2]) then return 0 end
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
local i = 4
for _ = 1, tonumber(ARGV[3]) do
  redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
  i = i + 2
end
redis.call('HSET', KEYS[1], 'computed_ts', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[1])
local n_shops = tonumber(ARGV[i])
i = i + 1
for _ = 1, n_shops do
  redis.call('ZADD', KEYS[2], ARGV[i + 1], ARGV[i])
  redis.call('HSET', KEYS[3], ARGV[i], ARGV[i + 2])
  i = i + 3
end
if n_shops > 0 then
  redis.call('EXPIRE', KEYS[2], ARGV[1])
  redis.call('EXPIRE', KEYS[3], ARGV[1])
end
return 1
"""


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class RedisHotCache:
    def __init__(self, redis: "Redis") -> None:
        self._redis = redis

    # -------------------------------------------------------------------------- band
    async def put_band(self, band: FairBand, ttl_s: int) -> None:
        key = band_key(band.area_id, band.product_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping=cast(dict[Any, Any], encode_band(band)))  # stub is invariant
            pipe.expire(key, ttl_s)
            await pipe.execute()

    async def get_band(self, area_id: str, product_id: str) -> FairBand | None:
        raw = await self._redis.hgetall(band_key(area_id, product_id))
        return decode_band(area_id, product_id, raw) if raw else None

    # --------------------------------------------------------------------- shop prices
    async def put_shop_prices(
        self, area_id: str, product_id: str, prices: list[ShopPrice], ttl_s: int
    ) -> None:
        """Replace both keys atomically (MULTI): stale shops disappear with the rewrite."""
        zkey, hkey = cheap_key(area_id, product_id), shop_price_key(area_id, product_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(zkey, hkey)
            if prices:
                pipe.zadd(zkey, {sp.shop_id: sp.price for sp in prices})
                pipe.hset(hkey, mapping={sp.shop_id: encode_shop_price(sp) for sp in prices})
                pipe.expire(zkey, ttl_s)
                pipe.expire(hkey, ttl_s)
            await pipe.execute()

    async def cheapest(
        self, area_id: str, product_id: str, max_price: float, limit: int
    ) -> list[ShopPrice]:
        """Verified shops strictly cheaper than ``max_price``, cheapest first, at most ``limit``."""
        if limit <= 0:
            return []
        members = await self._redis.zrangebyscore(
            cheap_key(area_id, product_id), "-inf", f"({max_price}", start=0, num=limit * 3
        )
        shop_ids = [_text(m) for m in cast(list[bytes | str], members)]
        if not shop_ids:
            return []
        raws = cast(
            list[Any],
            await self._redis.hmget(shop_price_key(area_id, product_id), shop_ids),
        )
        prices = [
            decode_shop_price(area_id, product_id, shop_id, raw)
            for shop_id, raw in zip(shop_ids, raws, strict=True)
            if raw is not None
        ]
        return [sp for sp in prices if sp.verified][:limit]

    async def shop_price(self, area_id: str, product_id: str, shop_id: str) -> ShopPrice | None:
        raw = await self._redis.hget(shop_price_key(area_id, product_id), shop_id)
        return decode_shop_price(area_id, product_id, shop_id, raw) if raw else None

    # ------------------------------------------------------------------------ snapshot
    async def put_snapshot(self, band: FairBand, prices: list[ShopPrice], ttl_s: int) -> bool:
        """Band + shop prices in one Lua call; a newer cached band (``computed_ts``) wins.

        Concurrent scorings commit in lock order but publish after commit, so publishes can
        arrive out of order; without this guard an older band could overwrite a newer one.
        """
        keys = [
            band_key(band.area_id, band.product_id),
            cheap_key(band.area_id, band.product_id),
            shop_price_key(band.area_id, band.product_id),
        ]
        fields = encode_band(band)
        args: list[str | bytes | float] = [ttl_s, band.computed_at.timestamp(), len(fields)]
        for name, value in fields.items():
            args += [name, value]
        args.append(len(prices))
        for sp in prices:
            args += [sp.shop_id, sp.price, encode_shop_price(sp)]
        written = await self._redis.eval(_PUT_SNAPSHOT, len(keys), *keys, *args)
        return bool(written)

    # ---------------------------------------------------------------------- geo (M04)
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
