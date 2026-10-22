import dataclasses
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest
import redis.asyncio as aioredis
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.catalog_yaml import load_catalog
from spm.adapters.memory import FixedClock, MemoryBus
from spm.adapters.postgres.catalog_seed import seed_catalog
from spm.adapters.postgres.tables import outbox, report_assessments, report_idempotency, reporters
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.adapters.redis.keys import EVENTS_STREAM
from spm.api.app import create_app
from spm.core.enums import BaseUnit
from spm.core.events import Event
from spm.core.models import GeoPoint, LocationFix, NormalizedPrice, RawPriceInput, Report, Reporter
from spm.ports import DuplicateClientReport
from spm.services.outbox import OutboxSweeper
from tests.conftest import client_for, make_settings
from tests.integration.conftest import TEST_REDIS_URL

pytestmark = pytest.mark.integration


@pytest.fixture
async def stack(
    database_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: aioredis.Redis,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    await seed_catalog(load_catalog(), session_factory, RedisHotCache(redis_client))
    app = create_app(
        make_settings(database_url=database_url, redis_url=TEST_REDIS_URL, event_bus="redis")
    )
    async for client in client_for(app):
        yield app, client


def _body(**kw: Any) -> dict[str, Any]:
    return {
        "client_report_id": str(uuid.uuid4()), "product_text": "piyaz", "shop_id": "shop-a",
        "price": 40.00, "quantity": 500, "unit": "gm",
        "observed_at": datetime.now(UTC).isoformat(),
        "location": {"lat": 23.7838, "lng": 90.4073, "accuracy_m": 25}, **kw,
    }  # fmt: skip


async def _token(client: httpx.AsyncClient, device: str | None = None) -> dict[str, Any]:
    response = await client.post("/v1/auth/device", json={"device_id": device or str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _stream_events(redis_client: aioredis.Redis) -> list[Event]:
    entries = cast(list[tuple[bytes, dict[bytes, bytes]]], await redis_client.xrange(EVENTS_STREAM))
    return [Event.from_json(fields[b"body"]) for _, fields in entries]


async def test_full_http_flow_on_postgres_and_redis(
    stack: tuple[FastAPI, httpx.AsyncClient],
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: aioredis.Redis,
) -> None:
    _, client = stack
    auth = await _token(client)
    headers = {"authorization": f"Bearer {auth['token']}"}
    body = _body()
    response = await client.post("/v1/reports", json=body, headers=headers)
    assert response.status_code == 202, response.text
    report_id = response.json()["report_id"]

    (event,) = await _stream_events(redis_client)
    assert event.type == "price.reported" and event.data["report_id"] == report_id

    async with session_factory() as s:
        assert (await s.execute(select(outbox.c.published_at))).scalar_one() is not None
        state = (await s.execute(select(report_assessments.c.scoring_state))).scalar_one()
        assert state == "queued"
        assert (await s.execute(select(report_idempotency.c.report_id))).scalar_one() == uuid.UUID(
            report_id
        )
        assert (await s.execute(select(reporters.c.reports_total))).scalar_one() == 1

    replay = await client.post("/v1/reports", json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["report_id"] == report_id
    status = await client.get(f"/v1/reports/{report_id}", headers=headers)
    assert status.json()["status"] == "queued"
    me = await client.get("/v1/me", headers=headers)
    assert me.json()["reports_total"] == 1
    assert len(await _stream_events(redis_client)) == 1


async def test_bus_failure_leaves_outbox_row_for_the_sweeper(
    stack: tuple[FastAPI, httpx.AsyncClient],
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: aioredis.Redis,
) -> None:
    app, client = stack
    broken = MemoryBus()
    broken.fail_publish = True
    real_bus = app.state.container.bus
    app.state.container.bus = broken

    auth = await _token(client)
    response = await client.post(
        "/v1/reports", json=_body(), headers={"authorization": f"Bearer {auth['token']}"}
    )
    assert response.status_code == 202  # accepted even though publishing failed
    async with session_factory() as s:
        assert (await s.execute(select(outbox.c.published_at))).scalar_one() is None
    assert await _stream_events(redis_client) == []

    later = FixedClock(datetime.now(UTC) + timedelta(minutes=1))
    sweeper = OutboxSweeper(real_bus, lambda: PgUnitOfWork(session_factory), later)
    assert await sweeper.sweep() == 1
    (event,) = await _stream_events(redis_client)
    assert event.data["report_id"] == response.json()["report_id"]
    async with session_factory() as s:
        assert (await s.execute(select(outbox.c.published_at))).scalar_one() is not None


async def test_auth_same_device_same_reporter(stack: tuple[FastAPI, httpx.AsyncClient]) -> None:
    _, client = stack
    device = str(uuid.uuid4())
    first, second = await _token(client, device), await _token(client, device)
    assert first["reporter_id"] == second["reporter_id"]
    bad = await client.get("/v1/me", headers={"authorization": "Bearer nope"})
    assert bad.status_code == 401


# ----------------------------------------------------------------------- repositories
async def test_pg_repos_round_trip(session_factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    reporter = Reporter(uuid.uuid4(), "device-1", 1.0, 1.0, 0, False, now)
    raw = RawPriceInput(None, "piyaz", Decimal("40.00"), Decimal("500.000"), "gm")
    report = Report(
        id=uuid.uuid4(), received_at=now, reporter_id=reporter.id, client_report_id=uuid.uuid4(),
        product_id="onion", shop_id="shop-a", area_id="demo-central",
        normalized=NormalizedPrice(
            "onion", BaseUnit.KG, Decimal("0.5000"), Decimal("80.0000"), raw
        ),
        observed_at=now, location=LocationFix(GeoPoint(23.7838, 90.4073), 25.0),
        distance_to_shop_m=33.6, location_verified=True, ip_hash="abc",
    )  # fmt: skip

    async with PgUnitOfWork(session_factory) as uow:
        await uow.reporters.create(reporter)
        await uow.reporters.create(
            dataclasses.replace(reporter, id=uuid.uuid4())
        )  # same device: no-op
        await uow.reports.add(report)
        await uow.commit()

    async with PgUnitOfWork(session_factory) as uow:
        stored = await uow.reports.get(report.id)
        assert stored is not None
        assert stored.location is not None
        assert stored.location.point.lat == pytest.approx(23.7838)
        # location and distance_to_shop_m round-trip through float32 (real) columns.
        assert stored.distance_to_shop_m == pytest.approx(33.6, abs=1e-4)
        assert (
            dataclasses.replace(
                stored, location=report.location, distance_to_shop_m=report.distance_to_shop_m
            )
            == report
        )
        assert await uow.reports.by_client_id(reporter.id, report.client_report_id) == report.id
        assert await uow.reports.get(uuid.uuid4()) is None

        loaded = await uow.reporters.get_by_device("device-1")
        assert loaded is not None and loaded.id == reporter.id and loaded.reports_total == 1

        with pytest.raises(DuplicateClientReport):
            await uow.reports.add(dataclasses.replace(report, id=uuid.uuid4()))
        await uow.rollback()

    async with PgUnitOfWork(session_factory) as uow:
        await uow.reporters.add_evidence(reporter.id, 3.0, 1.0)
        await uow.reporters.touch(reporter.id, now)
        await uow.reporters.touch(reporter.id, now + timedelta(minutes=30))  # within the hour
        await uow.commit()
    async with PgUnitOfWork(session_factory) as uow:
        assert await uow.reporters.trust_many([reporter.id, uuid.uuid4()]) == {
            reporter.id: pytest.approx(4 / 6)
        }
        updated = await uow.reporters.get(reporter.id)
        assert updated is not None and updated.last_seen_at == now
