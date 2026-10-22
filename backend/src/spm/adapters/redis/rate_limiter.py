"""Fixed-window rate limiter on Redis: ``spm:rl:{scope}:{key}:{floor(now / window)}``."""

import math

from redis.asyncio import Redis

from spm.adapters.redis.keys import rate_limit_key
from spm.ports import Clock


class RedisRateLimiter:
    def __init__(self, redis: "Redis", clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(self, scope: str, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        """Count one hit (rejected hits count too); return (allowed, retry_after_s or 0)."""
        now_s = self._clock.now().timestamp()
        bucket = math.floor(now_s / window_s)
        redis_key = rate_limit_key(scope, key, bucket)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_s, nx=True)  # TTL set on the first increment only
            count, _ = await pipe.execute()
        if int(count) <= limit:
            return True, 0
        return False, max(1, math.ceil((bucket + 1) * window_s - now_s))
