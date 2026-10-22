from datetime import UTC, datetime, timedelta, timezone

import pytest

from spm.core.clock import FixedClock, SystemClock


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC


def test_fixed_clock_advance_moves_time() -> None:
    clock = FixedClock(datetime(2026, 10, 10, 8, 0, tzinfo=UTC))
    clock.advance(hours=6, minutes=30)
    assert clock.now() == datetime(2026, 10, 10, 14, 30, tzinfo=UTC)
    clock.advance(days=1)
    assert clock.now() == datetime(2026, 10, 11, 14, 30, tzinfo=UTC)


def test_fixed_clock_normalizes_to_utc() -> None:
    dhaka = timezone(timedelta(hours=6))
    clock = FixedClock(datetime(2026, 10, 10, 14, 0, tzinfo=dhaka))
    assert clock.now() == datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 10, 10))
    clock = FixedClock(datetime(2026, 10, 10, tzinfo=UTC))
    with pytest.raises(ValueError):
        clock.set(datetime(2026, 10, 11))
