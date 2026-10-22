import math
from collections import defaultdict

from spm.ports.clock import Clock


class MemoryRateLimiter:
    """Fixed-window counter, the same semantics as the Redis ``spm:rl:{scope}:{id}:{bucket}`` keys.

    Every hit is counted, including rejected ones (like ``INCR``).
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._counts: defaultdict[tuple[str, str, int, int], int] = defaultdict(int)

    async def hit(self, scope: str, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        now_s = self._clock.now().timestamp()
        bucket = math.floor(now_s / window_s)
        self._counts[(scope, key, window_s, bucket)] += 1
        if self._counts[(scope, key, window_s, bucket)] <= limit:
            return True, 0
        window_end = (bucket + 1) * window_s
        return False, max(1, math.ceil(window_end - now_s))
