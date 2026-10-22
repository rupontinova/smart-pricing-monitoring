from datetime import UTC, datetime

from spm.adapters.memory import FixedClock, MemoryMetrics, MemoryRateLimiter, SequentialIds
from spm.ports import Clock, IdGenerator, Metrics, RateLimiter

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)


def test_fixed_clock_advance_works() -> None:
    clock = FixedClock(T0)
    assert clock.advance(hours=2) == datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
    assert clock.now() == datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def test_sequential_ids_monotonic_and_uuid7() -> None:
    ids = SequentialIds()
    generated = [ids.new() for _ in range(1000)]
    assert generated == sorted(generated)
    assert len(set(generated)) == 1000
    assert all(u.version == 7 and u.variant == "specified in RFC 4122" for u in generated)


def test_sequential_ids_deterministic() -> None:
    assert [SequentialIds().new() for _ in range(3)] == [SequentialIds().new()] * 3
    a, b = SequentialIds(), SequentialIds()
    assert [a.new() for _ in range(5)] == [b.new() for _ in range(5)]


def test_sequential_ids_embed_epoch_timestamp() -> None:
    first = SequentialIds(epoch=T0).new()
    assert first.int >> 80 == int(T0.timestamp() * 1000)


async def test_rate_limiter_blocks_after_limit_then_resets() -> None:
    clock = FixedClock(datetime(2026, 10, 10, 8, 0, 0, tzinfo=UTC))
    limiter = MemoryRateLimiter(clock)
    results = [await limiter.hit("report", "r1", limit=3, window_s=3600) for _ in range(4)]
    assert results[:3] == [(True, 0)] * 3
    assert results[3] == (False, 3600)

    clock.advance(minutes=59, seconds=30)
    assert await limiter.hit("report", "r1", 3, 3600) == (False, 30)
    clock.advance(seconds=30)
    assert await limiter.hit("report", "r1", 3, 3600) == (True, 0)


async def test_rate_limiter_isolates_scopes_and_keys() -> None:
    limiter = MemoryRateLimiter(FixedClock(T0))
    assert (await limiter.hit("report", "r1", 1, 60))[0]
    assert (await limiter.hit("report", "r2", 1, 60))[0]
    assert (await limiter.hit("auth", "r1", 1, 60))[0]
    assert not (await limiter.hit("report", "r1", 1, 60))[0]


def test_memory_metrics_records_calls() -> None:
    metrics = MemoryMetrics()
    metrics.count("reports_accepted", area="demo-central")
    metrics.count("reports_accepted", 2, area="other")
    metrics.gauge("band_width", 0.12)
    metrics.timing("score_ms", 4.5, stage="classify")
    assert metrics.total("reports_accepted") == 3
    assert metrics.total("reports_accepted", area="other") == 2
    assert [c.kind for c in metrics.calls] == ["count", "count", "gauge", "timing"]


def test_memory_adapters_satisfy_ports() -> None:
    # Static check: mypy verifies these assignments against the Protocols.
    clock: Clock = FixedClock(T0)
    ids: IdGenerator = SequentialIds()
    limiter: RateLimiter = MemoryRateLimiter(clock)
    metrics: Metrics = MemoryMetrics()
    assert all(x is not None for x in (clock, ids, limiter, metrics))
