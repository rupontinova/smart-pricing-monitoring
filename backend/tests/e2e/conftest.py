"""E2E fixtures: the integration fixtures (migrated Postgres, Redis DB 15) plus a ``World`` that
wires the real services, scoring consumer and an in-process ``MemoryBus``."""

import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.catalog_yaml import load_catalog
from spm.adapters.ids import UuidV7Generator
from spm.adapters.memory import FixedClock, MemoryBus, MemoryMetrics, MemoryRateLimiter
from spm.adapters.postgres.catalog_repo import PgCatalogReader
from spm.adapters.postgres.catalog_seed import seed_catalog
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.adapters.tokens import JwtTokens
from spm.container import Container
from spm.core.enums import Classification, ScoringState
from spm.core.events import EVENT_TYPES, Event
from spm.services.auth_service import AuthService
from spm.services.band_service import BandService
from spm.services.catalog_cache import CatalogCache
from spm.services.guardrail_service import GuardrailService
from spm.services.outbox import OutboxPublisher
from spm.services.report_service import ReportService, SubmitReport
from spm.workers.runtime import ConsumerSpec
from spm.workers.scoring import scoring_consumer
from tests.conftest import make_settings
from tests.integration.conftest import (  # noqa: F401  (fixtures re-exported for e2e tests)
    TEST_REDIS_URL,
    database_url,
    engine,
    redis_client,
    session_factory,
)

# "Now" for the world: today (so received_at falls in a real monthly partition), at noon UTC.
T0 = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


class World:
    def __init__(self, sf: async_sessionmaker[AsyncSession], redis: aioredis.Redis) -> None:
        self.sf = sf
        self.redis = redis
        self.clock = FixedClock(T0)
        self.bus = MemoryBus()
        self.metrics = MemoryMetrics()
        self.hot = RedisHotCache(redis)
        self.catalog = CatalogCache(PgCatalogReader(sf), self.clock)
        ids = UuidV7Generator()
        uow_factory = lambda: PgUnitOfWork(sf)  # noqa: E731
        self.container = Container(
            settings=make_settings(),
            clock=self.clock,
            ids=ids,
            catalog=self.catalog,
            uow_factory=uow_factory,
            hot_cache=self.hot,
            bus=self.bus,
            consumer=self.bus,
            metrics=self.metrics,
        )
        self.auth = AuthService(
            uow_factory, JwtTokens("e" * 32, 90, self.clock), ids, self.clock, None
        )
        self.reports = ReportService(
            GuardrailService(self.catalog, MemoryRateLimiter(self.clock), uow_factory, self.clock),
            uow_factory,
            OutboxPublisher(self.bus, uow_factory, self.clock),
            ids,
            self.clock,
        )
        self.scoring: ConsumerSpec = scoring_consumer(self.container)
        self.events: list[Event] = []

    def scoring_band_service(self) -> "BandService":
        return BandService(self.hot, lambda: PgUnitOfWork(self.sf), self.clock)

    def subscribe(self, *, scoring: bool = True) -> None:
        if scoring:
            self.bus.subscribe("scoring", self.scoring.types, self.scoring.handler)

        async def collect(event: Event) -> None:
            self.events.append(event)

        self.bus.subscribe("collector", set(EVENT_TYPES), collect)

    def counts(self) -> Counter[str]:
        return Counter(e.type for e in self.events)

    async def submit(
        self, shop: str, price: float, *, device: str | None = None, product: str = "onion"
    ) -> uuid.UUID:
        """One report observed now (1 kg at ``price``) from a (by default new) reporter; scored."""
        reporter = await self.auth.reporter_for_device(device or str(uuid.uuid4()))
        result = await self.reports.submit(
            reporter,
            SubmitReport(
                uuid.uuid4(),
                product,
                None,
                shop,
                Decimal(str(price)),
                Decimal(1),
                "kg",
                self.clock.now(),
                None,
                None,
            ),
            request_id=None,
        )
        await self.bus.drain()
        return result.report.id

    async def baseline(
        self, *, days: int = 3, start_days_ago: float = 3, price: float = 80.0
    ) -> None:
        """2 reports per shop per day at price ± 1, ending ``start_days_ago - days`` days ago."""
        shops = ("shop-a", "shop-b", "shop-c", "shop-d", "shop-e")
        self.clock.set(T0 - timedelta(days=start_days_ago))
        for day in range(days):
            for slot in range(2):
                for i, shop in enumerate(shops):
                    await self.submit(shop, price + ((i + day + slot) % 3 - 1))
                    self.clock.advance(minutes=20)
                self.clock.advance(hours=12 - 20 * len(shops) / 60)
        self.clock.set(T0)

    async def state(self, report_id: uuid.UUID) -> tuple[ScoringState, Classification | None]:
        async with PgUnitOfWork(self.sf) as uow:
            return await uow.assessments.get_state(report_id)


@pytest.fixture
async def world(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    redis_client: aioredis.Redis,  # noqa: F811
) -> AsyncIterator[World]:
    redis = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await seed_catalog(load_catalog(), session_factory, RedisHotCache(redis))
    w = World(session_factory, redis)
    yield w
    await redis.aclose()
