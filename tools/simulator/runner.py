"""Replay a ``SimStream`` through the real code path and collect what the system concluded.

``inprocess`` (default): the real services, scoring consumer, Postgres and Redis, with a
``FixedClock`` advanced to each report's submission time and a ``MemoryBus`` drained after every
report, so a run is deterministic. Rate limits stay on (``MemoryRateLimiter`` on the simulated
clock), which is what ``spam_attack`` measures.

``http``: posts every report to ``/v1/admin/reports/backfill`` of a running stack and waits for its
worker. The timeline is shifted to end now. Scoring there runs on the wall clock, so windows that
depend on "now" (freshness, market windows) see a compressed history: use it as a smoke test of a
live deployment, and ``inprocess`` for the numbers.
"""

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from simulator.generator import SimReport, SimStream
from simulator.maintenance import nightly
from spm.adapters.ids import UuidV7Generator
from spm.adapters.memory import FixedClock, MemoryBus, MemoryMetrics, MemoryRateLimiter
from spm.adapters.postgres.catalog_repo import PgCatalogReader
from spm.adapters.postgres.tables import anomalies, report_assessments
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.adapters.tokens import JwtTokens
from spm.config import Settings
from spm.container import Container
from spm.core.enums import Classification, ScoringState
from spm.core.errors import SpmError
from spm.core.models import GeoPoint, LocationFix, Reporter
from spm.services.auth_service import AuthService
from spm.services.catalog_cache import CatalogCache
from spm.services.guardrail_service import GuardrailService
from spm.services.outbox import OutboxPublisher
from spm.services.report_service import ReportService, SubmitReport
from spm.workers.scoring import scoring_consumer

Progress = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class Outcome:
    seq: int
    report_id: UUID | None
    duplicate: bool = False
    rejected: str | None = None  # error code from the guardrails / rate limiter


@dataclass(frozen=True, slots=True)
class Prediction:
    state: ScoringState
    classification: Classification | None


@dataclass(frozen=True, slots=True)
class AnomalyRow:
    id: UUID
    kind: str
    direction: str | None
    product_id: str
    shop_id: str | None
    detected_at: datetime
    report_ids: tuple[UUID, ...]
    magnitude_pct: float | None


@dataclass(frozen=True, slots=True)
class BandSnapshot:
    """The stored band at the end of a simulated day."""

    day: int
    at: datetime
    product: str
    p50: float | None
    confidence: str | None
    mean_age_h: float | None
    newest_obs_at: datetime | None
    fair_price: float


@dataclass(slots=True)
class RunResult:
    stream: SimStream
    mode: str
    outcomes: list[Outcome] = field(default_factory=list)
    predictions: dict[UUID, Prediction] = field(default_factory=dict)
    anomalies: list[AnomalyRow] = field(default_factory=list)
    bands: list[BandSnapshot] = field(default_factory=list)
    elapsed_s: float = 0.0
    dead_letters: int = 0


def _command(r: SimReport, shift: timedelta = timedelta(0)) -> SubmitReport:
    loc = r.location
    return SubmitReport(
        client_report_id=r.client_report_id,
        product_id=r.product,
        product_text=None,
        shop_id=r.shop,
        price=r.price,
        quantity=r.quantity,
        unit=r.unit,
        observed_at=r.observed_at + shift,
        location=LocationFix(GeoPoint(loc[0], loc[1]), loc[2]) if loc else None,
        ip_hash=r.ip_hash,
    )


async def _predictions(
    sf: async_sessionmaker[AsyncSession], ids: Sequence[UUID]
) -> dict[UUID, Prediction]:
    out: dict[UUID, Prediction] = {}
    async with sf() as session:
        for i in range(0, len(ids), 1000):
            rows = await session.execute(
                select(
                    report_assessments.c.report_id,
                    report_assessments.c.scoring_state,
                    report_assessments.c.classification,
                ).where(report_assessments.c.report_id.in_(ids[i : i + 1000]))
            )
            for rid, state, cls in rows:
                out[rid] = Prediction(
                    ScoringState(state), Classification(cls) if cls is not None else None
                )
    return out


async def _anomalies(
    sf: async_sessionmaker[AsyncSession], area: str, since: datetime | None = None
) -> list[AnomalyRow]:
    query = select(anomalies).where(anomalies.c.area_id == area).order_by(anomalies.c.detected_at)
    if since is not None:
        query = query.where(anomalies.c.detected_at >= since)
    async with sf() as session:
        rows = (await session.execute(query)).mappings().all()
    return [
        AnomalyRow(
            r["id"],
            str(r["kind"]),
            r["direction"],
            r["product_id"],
            r["shop_id"],
            r["detected_at"],
            tuple(r["report_ids"]),
            r["magnitude_pct"],
        )
        for r in rows
    ]


# ------------------------------------------------------------------------------- in-process
class InProcessRunner:
    def __init__(
        self,
        stream: SimStream,
        session_factory: async_sessionmaker[AsyncSession],
        redis: aioredis.Redis,
        settings: Settings,
    ) -> None:
        self.stream = stream
        self.sf = session_factory
        self.clock = FixedClock(stream.start)
        self.bus = MemoryBus()
        ids = UuidV7Generator()
        uow_factory = lambda: PgUnitOfWork(session_factory)  # noqa: E731
        hot = RedisHotCache(redis)
        # The catalog never changes during a replay: give its cache a clock that stands still, or
        # every jump of the simulated clock would reload it (5 extra queries per report).
        catalog = CatalogCache(PgCatalogReader(session_factory), FixedClock(stream.start))
        container = Container(
            settings=settings,
            clock=self.clock,
            ids=ids,
            catalog=catalog,
            uow_factory=uow_factory,
            hot_cache=hot,
            bus=self.bus,
            consumer=self.bus,
            metrics=MemoryMetrics(),
        )
        self.auth = AuthService(
            uow_factory,
            JwtTokens(settings.jwt_secret.get_secret_value(), 90, self.clock),
            ids,
            self.clock,
            None,
        )
        self.reports = ReportService(
            GuardrailService(catalog, MemoryRateLimiter(self.clock), uow_factory, self.clock, hot),
            uow_factory,
            OutboxPublisher(self.bus, uow_factory, self.clock),
            ids,
            self.clock,
        )
        scoring = scoring_consumer(container)
        self.bus.subscribe(scoring.group, scoring.types, scoring.handler)
        self.catalog = catalog
        self._reporters: dict[str, Reporter] = {}
        self._half_lives: dict[str, int] | None = None

    async def _reporter(self, device_id: str) -> Reporter:
        if device_id not in self._reporters:
            self._reporters[device_id] = await self.auth.reporter_for_device(device_id)
        return self._reporters[device_id]

    async def _end_of_day(self, day: int, result: RunResult) -> None:
        """Nightly maintenance (the M17 stand-in), then a snapshot of every product's band."""
        s = self.stream.scenario
        at = self.stream.start + timedelta(days=day + 1)
        if self._half_lives is None:
            catalog = await self.catalog.ensure_fresh()
            self._half_lives = {
                p: found.half_life_h
                for p in s.products
                if (found := catalog.product(p)) is not None
            }
        await nightly(self.sf, at, self._half_lives)
        async with PgUnitOfWork(self.sf) as uow:
            for product in s.products:
                band = await uow.bands.get_band(product, s.area)
                result.bands.append(
                    BandSnapshot(
                        day,
                        at,
                        product,
                        band.p50 if band else None,
                        str(band.confidence) if band else None,
                        band.mean_age_h if band else None,
                        band.newest_obs_at if band else None,
                        self.stream.market.fair_price(product, at),
                    )
                )

    async def run(self, progress: Progress | None = None) -> RunResult:
        s = self.stream.scenario
        result = RunResult(self.stream, "inprocess")
        started = time.perf_counter()
        day = 0
        total = len(self.stream.reports)
        for r in self.stream.reports:
            while r.submitted_at >= self.stream.start + timedelta(days=day + 1) and day < s.days:
                await self._end_of_day(day, result)
                day += 1
            self.clock.set(r.submitted_at)
            reporter = await self._reporter(r.device_id)
            try:
                submitted = await self.reports.submit(
                    reporter,
                    _command(r),
                    request_id=None,
                    allow_backfill=True,
                    enforce_rate_limits=True,
                )
            except SpmError as exc:
                result.outcomes.append(Outcome(r.seq, None, rejected=exc.code))
            else:
                result.outcomes.append(
                    Outcome(r.seq, submitted.report.id, duplicate=submitted.duplicate)
                )
            await self.bus.drain()
            if progress is not None:
                progress(r.seq + 1, total)
        while day < s.days:
            self.clock.set(self.stream.start + timedelta(days=day + 1))
            await self._end_of_day(day, result)
            day += 1
        result.elapsed_s = time.perf_counter() - started
        result.dead_letters = len(self.bus.dlq)
        ids = [o.report_id for o in result.outcomes if o.report_id and not o.duplicate]
        result.predictions = await _predictions(self.sf, ids)
        result.anomalies = await _anomalies(self.sf, s.area)
        return result


async def run_inprocess(
    stream: SimStream,
    session_factory: async_sessionmaker[AsyncSession],
    redis: aioredis.Redis,
    settings: Settings,
    progress: Progress | None = None,
) -> RunResult:
    """Replay on a migrated, seeded database (the caller prepares and cleans it)."""
    return await InProcessRunner(stream, session_factory, redis, settings).run(progress)


# ------------------------------------------------------------------------------- http
def _payload(r: SimReport, shift: timedelta) -> dict[str, object]:
    body: dict[str, object] = {
        "client_report_id": str(r.client_report_id),
        "product": r.product,
        "shop_id": r.shop,
        "price": str(r.price),
        "quantity": str(r.quantity),
        "unit": r.unit,
        "observed_at": (r.observed_at + shift).isoformat(),
        "reporter_device_id": r.device_id,
    }
    if r.location:
        body["location"] = {"lat": r.location[0], "lng": r.location[1], "accuracy_m": r.location[2]}
    return body


async def run_http(
    stream: SimStream,
    base_url: str,
    admin_key: str,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    wait_s: float = 120.0,
    progress: Progress | None = None,
) -> RunResult:
    """Post to a live stack, wait for its worker, then read the outcome from its database."""
    result = RunResult(stream, "http")
    run_started = datetime.now(UTC)
    shift = run_started - stream.end
    started = time.perf_counter()
    headers = {"X-Admin-Key": admin_key}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
        for r in stream.reports:
            resp = await client.post("/v1/admin/reports/backfill", json=_payload(r, shift))
            if resp.status_code in (200, 202):
                body = resp.json()
                result.outcomes.append(
                    Outcome(r.seq, UUID(body["report_id"]), duplicate=body["duplicate"])
                )
            elif resp.status_code in (401, 403):
                raise RuntimeError(f"backfill refused ({resp.status_code}): {resp.text}")
            else:
                code = resp.json().get("code", f"HTTP_{resp.status_code}")
                result.outcomes.append(Outcome(r.seq, None, rejected=code))
            if progress is not None:
                progress(r.seq + 1, len(stream.reports))

    ids = [o.report_id for o in result.outcomes if o.report_id and not o.duplicate]
    deadline = time.monotonic() + wait_s
    while True:
        result.predictions = await _predictions(session_factory, ids)
        queued = sum(p.state is ScoringState.QUEUED for p in result.predictions.values())
        if queued == 0 or time.monotonic() > deadline:
            break
        await asyncio.sleep(1.0)
    result.elapsed_s = time.perf_counter() - started

    # Anomalies carry wall-clock times here: map each back to the newest report behind it.
    by_id = {o.report_id: stream.reports[o.seq] for o in result.outcomes if o.report_id}
    for a in await _anomalies(session_factory, stream.scenario.area, run_started):
        seen = [by_id[rid].submitted_at for rid in a.report_ids if rid in by_id]
        detected = max(seen) if seen else a.detected_at - shift
        result.anomalies.append(
            AnomalyRow(
                a.id,
                a.kind,
                a.direction,
                a.product_id,
                a.shop_id,
                detected,
                a.report_ids,
                a.magnitude_pct,
            )
        )
    return result
