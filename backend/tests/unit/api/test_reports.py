import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from spm.adapters.memory import (
    FixedClock,
    MemoryBus,
    MemoryCatalogReader,
    MemoryMetrics,
    MemoryRateLimiter,
    MemoryShopGeoIndex,
    MemoryUnitOfWork,
    SequentialIds,
)
from spm.adapters.tokens import JwtTokens
from spm.api.app import create_app
from spm.config import Settings
from spm.container import Container
from spm.core.enums import Classification, ScoringState
from spm.core.models import ShopPrice
from spm.core.privacy import hash_ip
from spm.services.catalog_cache import CatalogCache
from tests.conftest import client_for, make_settings

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
ADMIN_KEY = "test-admin-key"
SHOP_A_NEAR = {"lat": 23.7838, "lng": 90.4073, "accuracy_m": 25}  # ~34 m from shop-a


class Env:
    """The app on memory adapters, plus handles to inspect them."""

    def __init__(self, settings: Settings) -> None:
        self.clock = FixedClock(T0)
        self.bus = MemoryBus()
        self.uow = MemoryUnitOfWork()
        self.metrics = MemoryMetrics()
        self.settings = settings
        self.container = Container(
            settings=settings,
            clock=self.clock,
            ids=SequentialIds(),
            catalog=CatalogCache(MemoryCatalogReader.from_seeds(), self.clock),
            shop_geo=MemoryShopGeoIndex(),
            rate_limiter=MemoryRateLimiter(self.clock),
            bus=self.bus,
            consumer=self.bus,
            uow_factory=lambda: self.uow,
            tokens=JwtTokens("t" * 32, 90, self.clock),
            metrics=self.metrics,
        )
        self.client: httpx.AsyncClient

    async def token(self, device: str | None = None, ip: str = "198.51.100.1") -> str:
        response = await self.client.post(
            "/v1/auth/device",
            json={"device_id": device or str(uuid.uuid4())},
            headers={"x-forwarded-for": ip},
        )
        assert response.status_code == 200, response.text
        return str(response.json()["token"])

    async def report(self, token: str, **overrides: Any) -> httpx.Response:
        body: dict[str, Any] = {
            "client_report_id": str(uuid.uuid4()),
            "product_text": "piyaz",
            "shop_id": "shop-a",
            "price": 40.00,
            "quantity": 500,
            "unit": "gm",
            "observed_at": self.clock.now().isoformat(),
            "location": SHOP_A_NEAR,
        }
        headers = overrides.pop("headers", {})
        body.update(overrides)
        body = {k: v for k, v in body.items() if v is not ...}
        return await self.client.post(
            "/v1/reports", json=body, headers={"authorization": f"Bearer {token}", **headers}
        )


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    e = Env(make_settings(admin_api_key=ADMIN_KEY, ip_hash_salt="pepper"))
    async for client in client_for(create_app(container=e.container)):
        e.client = client
        yield e


# --------------------------------------------------------------------------- submit
async def test_happy_path_202_event_and_queued_assessment(env: Env) -> None:
    token = await env.token()
    response = await env.report(token, headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued" and body["duplicate"] is False
    assert body["normalized"] == {
        "product_id": "onion", "price_per_base_unit": 80.0, "base_unit": "kg", "base_quantity": 0.5,
    }  # fmt: skip
    assert body["location_verified"] is True
    assert body["distance_to_shop_m"] == pytest.approx(33.6, abs=1)

    (event,) = env.bus.published
    report_id = uuid.UUID(body["report_id"])
    assert event.type == "price.reported"
    assert event.correlation_id == response.headers["x-request-id"]
    assert event.data["report_id"] == str(report_id)
    assert event.data["norm_price"] == "80.0000"
    assert event.data["area_id"] == "demo-central"

    assert env.uow.reports.states[report_id] == (ScoringState.QUEUED, None)
    stored = env.uow.reports.by_id[report_id]
    assert stored.ip_hash == hash_ip("pepper", "203.0.113.7")  # first X-Forwarded-For hop
    assert next(iter(env.uow.reporters.by_id.values())).reports_total == 1
    assert all(published is not None for _, published, _ in env.uow.outbox.rows.values())
    assert env.metrics.total("reports_received") == 1


async def test_same_client_report_id_replayed_returns_200_and_no_second_event(env: Env) -> None:
    token = await env.token()
    client_id = str(uuid.uuid4())
    first = await env.report(token, client_report_id=client_id)
    second = await env.report(token, client_report_id=client_id, price=99.0)  # body ignored
    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["report_id"] == first.json()["report_id"]
    assert second.json()["duplicate"] is True
    assert len(env.bus.published) == 1
    assert env.metrics.total("reports_duplicate") == 1


async def test_near_identical_price_within_6h_is_duplicate_nothing_stored(env: Env) -> None:
    token = await env.token()
    first = await env.report(token)
    env.clock.advance(hours=2)
    second = await env.report(token, price=40.20, observed_at=env.clock.now().isoformat())
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["report_id"] == first.json()["report_id"]
    assert len(env.uow.reports.by_id) == 1 and len(env.bus.published) == 1


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"product_text": "xyz"}, 422, "UNKNOWN_PRODUCT"),
        (
            {"product_text": None, "product": "onion", "unit": "l", "quantity": 1},
            422,
            "UNIT_MISMATCH",
        ),
        ({"unit": "bag"}, 422, "UNKNOWN_UNIT"),
        ({"unit": "kg"}, 422, "QUANTITY_OUT_OF_RANGE"),  # 500 kg
        ({"observed_at": (T0 - timedelta(days=8)).isoformat()}, 422, "OBSERVED_AT_OUT_OF_RANGE"),
        ({"shop_id": "shop-z"}, 422, "UNKNOWN_SHOP"),
        (
            {"product_text": "eggs", "price": 12, "quantity": 1, "unit": "dozen"},
            422,
            "PRICE_OUT_OF_BOUNDS",
        ),
        ({"location": {"lat": 23.80, "lng": 90.4073}}, 422, "TOO_FAR_FROM_SHOP"),
        ({"product_text": None}, 422, "VALIDATION_ERROR"),
        ({"price": 40.005}, 422, "VALIDATION_ERROR"),
        ({"observed_at": "2026-10-10T08:00:00"}, 422, "VALIDATION_ERROR"),  # no timezone
        ({"unexpected": 1}, 422, "VALIDATION_ERROR"),
    ],
)
async def test_guardrail_errors_map_to_problem_codes(
    env: Env, overrides: dict[str, Any], status: int, code: str
) -> None:
    response = await env.report(await env.token(), **overrides)
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == code
    assert env.bus.published == []
    if code != "VALIDATION_ERROR":  # schema errors happen before the service
        assert env.metrics.total("reports_rejected", code=code) == 1


async def test_quantity_error_includes_hint(env: Env) -> None:
    response = await env.report(await env.token(), unit="kg")
    assert response.json()["hints"][0]["message"] == "Did you mean 500 g instead of 500 kg?"


async def test_31st_report_in_an_hour_is_rate_limited(env: Env) -> None:
    token = await env.token()
    for i in range(30):
        ok = await env.report(token, price=40 + i)  # distinct prices: not duplicates
        assert ok.status_code == 202, ok.text
    limited = await env.report(token, price=100)
    assert limited.status_code == 429
    assert limited.json()["code"] == "RATE_LIMITED"
    assert limited.headers["retry-after"] == "3600"


# ----------------------------------------------------------------------------- auth
async def test_same_device_returns_same_reporter(env: Env) -> None:
    device = str(uuid.uuid4())
    a = await env.client.post("/v1/auth/device", json={"device_id": device})
    b = await env.client.post("/v1/auth/device", json={"device_id": device})
    assert a.json()["reporter_id"] == b.json()["reporter_id"]
    assert a.json()["expires_at"] == (T0 + timedelta(days=90)).isoformat().replace("+00:00", "Z")
    assert len(env.uow.reporters.by_id) == 1


async def test_device_id_must_be_uuid(env: Env) -> None:
    response = await env.client.post("/v1/auth/device", json={"device_id": "my-phone"})
    assert response.status_code == 422


@pytest.mark.parametrize("header", [None, "Bearer", "Basic abc", "Bearer not-a-jwt"])
async def test_missing_or_invalid_token_is_401(env: Env, header: str | None) -> None:
    headers = {"authorization": header} if header else {}
    response = await env.client.get("/v1/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_token_signed_with_other_secret_is_401(env: Env) -> None:
    forged, _ = JwtTokens("o" * 32, 90, env.clock).issue(uuid.uuid4(), "dev")
    response = await env.client.get("/v1/me", headers={"authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_expired_token_is_401(env: Env) -> None:
    token = await env.token()
    env.clock.advance(days=90, seconds=1)
    response = await env.client.get("/v1/me", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Token expired."


async def test_banned_reporter_is_403(env: Env) -> None:
    device = str(uuid.uuid4())
    token = await env.token(device)
    reporter = next(iter(env.uow.reporters.by_id.values()))
    env.uow.reporters.ban(reporter.id)
    me = await env.client.get("/v1/me", headers={"authorization": f"Bearer {token}"})
    assert me.status_code == 403 and me.json()["code"] == "FORBIDDEN"
    again = await env.client.post("/v1/auth/device", json={"device_id": device})
    assert again.status_code == 403


async def test_auth_rate_limited_per_ip(env: Env) -> None:
    for _ in range(20):
        await env.token(ip="192.0.2.50")
    response = await env.client.post(
        "/v1/auth/device", json={"device_id": str(uuid.uuid4())},
        headers={"x-forwarded-for": "192.0.2.50"},
    )  # fmt: skip
    assert response.status_code == 429
    assert (await env.client.post(
        "/v1/auth/device", json={"device_id": str(uuid.uuid4())},
        headers={"x-forwarded-for": "192.0.2.51"},
    )).status_code == 200  # fmt: skip


async def test_last_seen_updated_at_most_hourly(env: Env) -> None:
    token = await env.token()
    headers = {"authorization": f"Bearer {token}"}
    await env.client.get("/v1/me", headers=headers)
    reporter_id = next(iter(env.uow.reporters.by_id))
    assert env.uow.reporters.by_id[reporter_id].last_seen_at == T0
    env.clock.advance(minutes=30)
    await env.client.get("/v1/me", headers=headers)
    assert env.uow.reporters.by_id[reporter_id].last_seen_at == T0
    env.clock.advance(minutes=31)
    await env.client.get("/v1/me", headers=headers)
    assert env.uow.reporters.by_id[reporter_id].last_seen_at == env.clock.now()


# ----------------------------------------------------------------------- read routes
async def test_me(env: Env) -> None:
    token = await env.token()
    await env.report(token)
    me = (await env.client.get("/v1/me", headers={"authorization": f"Bearer {token}"})).json()
    assert me["trust"] == 0.5
    assert me["reports_total"] == 1
    assert me["created_at"].startswith("2026-10-10T08:00:00")


async def test_report_status_own_only(env: Env) -> None:
    mine, theirs = await env.token(), await env.token()
    report_id = (await env.report(mine)).json()["report_id"]
    ok = await env.client.get(
        f"/v1/reports/{report_id}", headers={"authorization": f"Bearer {mine}"}
    )
    assert ok.json() == {
        "report_id": report_id, "status": "queued", "classification": None, "verified": False,
    }  # fmt: skip
    other = await env.client.get(
        f"/v1/reports/{report_id}", headers={"authorization": f"Bearer {theirs}"}
    )
    assert other.status_code == 404
    missing = await env.client.get(
        f"/v1/reports/{uuid.uuid4()}", headers={"authorization": f"Bearer {mine}"}
    )
    assert missing.status_code == 404


@pytest.mark.parametrize(
    ("classification", "shop_verified", "expected"),
    [
        (Classification.NORMAL, True, True),
        (Classification.SHOP_OUTLIER, True, True),
        (Classification.NORMAL, False, False),
        (Classification.PENDING, True, False),
        (Classification.BAD_REPORT, True, False),
    ],
)
async def test_report_verified_flag(
    env: Env, classification: Classification, shop_verified: bool, expected: bool
) -> None:
    token = await env.token()
    report_id = uuid.UUID((await env.report(token)).json()["report_id"])
    env.uow.assessments.set_state(report_id, ScoringState.SCORED, classification)
    await env.uow.bands.upsert_shop_price(
        ShopPrice("onion", "shop-a", "demo-central", 80.0, shop_verified, 2.0, 2, T0, T0)
    )
    body = (
        await env.client.get(
            f"/v1/reports/{report_id}", headers={"authorization": f"Bearer {token}"}
        )
    ).json()
    assert body["status"] == "scored"
    assert body["classification"] == classification.value
    assert body["verified"] is expected


# --------------------------------------------------------------------------- backfill
def _backfill_body(env: Env, **kw: Any) -> dict[str, Any]:
    return {
        "client_report_id": str(uuid.uuid4()), "product": "onion", "shop_id": "shop-b",
        "price": 70, "quantity": 1, "unit": "kg",
        "observed_at": (env.clock.now() - timedelta(days=30)).isoformat(),
        "reporter_device_id": "sim-reporter-1", **kw,
    }  # fmt: skip


@pytest.mark.parametrize("key", [None, "wrong"])
async def test_backfill_requires_admin_key(env: Env, key: str | None) -> None:
    headers = {"x-admin-key": key} if key else {}
    response = await env.client.post(
        "/v1/admin/reports/backfill", json=_backfill_body(env), headers=headers
    )
    assert response.status_code == 403


async def test_backfill_accepts_old_observations_without_rate_limits(env: Env) -> None:
    for i in range(35):
        response = await env.client.post(
            "/v1/admin/reports/backfill",
            json=_backfill_body(env, price=60 + i),
            headers={"x-admin-key": ADMIN_KEY},
        )
        assert response.status_code == 202, response.text
    assert len(env.uow.reporters.by_id) == 1  # one simulator reporter, created on demand
    assert len(env.bus.published) == 35


@pytest.mark.parametrize(("allow", "status"), [(False, 403), (True, 202)])
async def test_backfill_on_aws_needs_explicit_flag(allow: bool, status: int) -> None:
    settings = make_settings(
        env="aws", jwt_secret="a" * 32, admin_api_key=ADMIN_KEY, ip_hash_salt="c" * 32,
        allow_backfill=allow,
    )  # fmt: skip
    env = Env(settings)
    async for client in client_for(create_app(container=env.container)):
        response = await client.post(
            "/v1/admin/reports/backfill",
            json=_backfill_body(env),
            headers={"x-admin-key": ADMIN_KEY},
        )
    assert response.status_code == status


async def test_reports_unavailable_without_database(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/auth/device", json={"device_id": str(uuid.uuid4())})
    assert response.status_code == 503
