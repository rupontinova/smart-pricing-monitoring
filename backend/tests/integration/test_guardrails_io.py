import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import redis.asyncio as aioredis
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.catalog_yaml import load_catalog
from spm.adapters.memory import FixedClock
from spm.adapters.postgres.catalog_repo import PgCatalogReader
from spm.adapters.postgres.catalog_seed import seed_catalog
from spm.adapters.postgres.tables import reports
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.adapters.redis.keys import rate_limit_key
from spm.adapters.redis.rate_limiter import RedisRateLimiter
from spm.core.errors import RateLimited
from spm.core.models import RawPriceInput
from spm.services.catalog_cache import CatalogCache
from spm.services.guardrail_service import GuardrailService

pytestmark = pytest.mark.integration

REPORTER = uuid.UUID(int=42)


async def test_rate_limiter_allows_30_denies_31st(redis_client: aioredis.Redis) -> None:
    clock = FixedClock(datetime(2026, 10, 10, 12, 15, 0, tzinfo=UTC))  # 45 min left in the hour
    limiter = RedisRateLimiter(redis_client, clock)
    results = [await limiter.hit("report", "r1", 30, 3600) for _ in range(31)]
    assert results[:30] == [(True, 0)] * 30
    allowed, retry_after = results[30]
    assert not allowed
    assert retry_after == 45 * 60

    bucket = int(clock.now().timestamp()) // 3600
    key = rate_limit_key("report", "r1", bucket)
    assert await redis_client.get(key) == b"31"
    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 3600

    # Other keys and a new window are independent.
    assert (await limiter.hit("report", "r2", 30, 3600))[0]
    clock.advance(minutes=45)
    assert (await limiter.hit("report", "r1", 30, 3600)) == (True, 0)


async def test_rate_limiter_ttl_set_once_and_key_expires(redis_client: aioredis.Redis) -> None:
    clock = FixedClock(datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC))
    limiter = RedisRateLimiter(redis_client, clock)
    await limiter.hit("auth", "ip1", 5, 2)
    key = rate_limit_key("auth", "ip1", int(clock.now().timestamp()) // 2)
    first_ttl = await redis_client.pttl(key)
    await asyncio.sleep(0.3)
    await limiter.hit("auth", "ip1", 5, 2)
    assert await redis_client.pttl(key) < first_ttl  # not refreshed by later hits
    await asyncio.sleep(2.2)
    assert await redis_client.exists(key) == 0


def _report(
    observed_at: datetime, price: str, *, shop: str = "shop-a", reporter: uuid.UUID = REPORTER
) -> dict[str, object]:
    return {
        "id": uuid.uuid4(), "received_at": observed_at, "reporter_id": reporter,
        "client_report_id": uuid.uuid4(), "product_id": "onion", "shop_id": shop,
        "area_id": "demo-central", "raw_price": Decimal(price), "raw_quantity": 1, "raw_unit": "kg",
        "norm_price": Decimal(price), "base_quantity": 1, "observed_at": observed_at,
        "location_verified": False,
    }  # fmt: skip


async def test_find_recent_duplicate(session_factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC)
    rows = [
        _report(now - timedelta(hours=1), "60.00"),
        _report(now - timedelta(hours=3), "61.00"),
        _report(now - timedelta(hours=7), "60.00"),  # outside 6 h
        _report(now - timedelta(hours=1), "60.00", shop="shop-b"),
        _report(now - timedelta(hours=1), "60.00", reporter=uuid.uuid4()),
    ]
    async with session_factory() as session:
        await session.execute(insert(reports), rows)
        await session.commit()
    async with PgUnitOfWork(session_factory) as uow:
        found = await uow.reports.find_recent_duplicate(
            REPORTER, "onion", "shop-a", now - timedelta(hours=6)
        )
    assert found == [(rows[0]["id"], Decimal("60.0000")), (rows[1]["id"], Decimal("61.0000"))]


async def test_guardrail_service_on_postgres_and_redis(
    session_factory: async_sessionmaker[AsyncSession], redis_client: aioredis.Redis
) -> None:
    await seed_catalog(load_catalog(), session_factory, RedisHotCache(redis_client))
    clock = FixedClock(datetime.now(UTC).replace(microsecond=0))
    service = GuardrailService(
        CatalogCache(PgCatalogReader(session_factory), clock),
        RedisRateLimiter(redis_client, clock),
        lambda: PgUnitOfWork(session_factory),
        clock,
    )
    earlier = _report(clock.now() - timedelta(hours=1), "80.00")
    async with session_factory() as session:
        await session.execute(insert(reports), [earlier])
        await session.commit()

    raw = RawPriceInput(None, "piyaz", Decimal(40), Decimal(500), "gm")  # 80/kg
    admission = await service.admit(REPORTER, raw, "shop-a", clock.now(), None)
    assert admission.normalized.norm_price == Decimal("80.0000")
    assert admission.duplicate_of == earlier["id"]

    for _ in range(29):
        await service.admit(REPORTER, raw, "shop-a", clock.now(), None)
    with pytest.raises(RateLimited):
        await service.admit(REPORTER, raw, "shop-a", clock.now(), None)
