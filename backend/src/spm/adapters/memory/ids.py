import uuid
from datetime import UTC, datetime

DEFAULT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_RAND_B_BITS = 62


class SequentialIds:
    """Deterministic, strictly increasing UUIDv7s.

    The n-th id (0-based) has timestamp ``epoch + n ms`` and ``n`` in its random bits, so test
    output is reproducible and ids sort in creation order.
    """

    def __init__(self, epoch: datetime = DEFAULT_EPOCH) -> None:
        self._epoch_ms = int(epoch.timestamp() * 1000)
        self._n = 0

    def new(self) -> uuid.UUID:
        n = self._n
        self._n += 1
        ts_ms = self._epoch_ms + n
        value = (
            (ts_ms & 0xFFFF_FFFF_FFFF) << 80
            | 0x7 << 76  # version 7
            | 0b10 << 62  # RFC 9562 variant
            | (n & ((1 << _RAND_B_BITS) - 1))
        )
        return uuid.UUID(int=value)
