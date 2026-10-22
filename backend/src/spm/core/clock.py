"""Clocks. Core and services never read the wall clock directly; they receive one of these.

The ``Clock`` protocol itself lives in ``spm.ports`` (M02).
"""

from datetime import UTC, datetime, timedelta


class SystemClock:
    """The real clock: always returns an aware UTC ``datetime``."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A settable clock for tests and the simulator."""

    def __init__(self, now: datetime) -> None:
        self._now = _require_aware(now)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = _require_aware(now)

    def advance(
        self,
        *,
        days: float = 0,
        hours: float = 0,
        minutes: float = 0,
        seconds: float = 0,
    ) -> datetime:
        self._now += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return self._now


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("FixedClock needs a timezone-aware datetime")
    return value.astimezone(UTC)
