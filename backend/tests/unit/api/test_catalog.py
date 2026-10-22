from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from spm.adapters.memory import FixedClock, MemoryCatalogReader, MemoryShopGeoIndex, SequentialIds
from spm.api.app import create_app
from spm.config import Settings
from spm.container import Container
from spm.services.catalog_cache import CatalogCache
from tests.conftest import client_for

CENTER = {"lat": 23.7808, "lng": 90.4073}


def memory_container(settings: Settings, *, geo: bool = True) -> Container:
    clock = FixedClock(datetime(2026, 10, 10, 8, 0, tzinfo=UTC))
    return Container(
        settings=settings,
        clock=clock,
        ids=SequentialIds(),
        catalog=CatalogCache(MemoryCatalogReader.from_seeds(), clock),
        shop_geo=MemoryShopGeoIndex() if geo else None,
    )


@pytest.fixture
async def catalog_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for client in client_for(create_app(container=memory_container(settings))):
        yield client


async def test_products_lists_ten_active_products_in_order(
    catalog_client: httpx.AsyncClient,
) -> None:
    response = await catalog_client.get("/v1/products")
    assert response.status_code == 200
    products = response.json()
    assert len(products) == 10
    assert products[0] == {
        "id": "onion",
        "name": "Onion",
        "base_unit": "kg",
        "category": "vegetable",
        "aliases": ["peyaj", "piyaj", "piyaz", "pyaz"],
        "unit_presets": [
            {"q": 250, "u": "g"}, {"q": 500, "u": "g"}, {"q": 1, "u": "kg"},
            {"q": 2, "u": "kg"}, {"q": 5, "u": "kg"},
        ],
    }  # fmt: skip


@pytest.mark.parametrize(
    ("product_id", "units"),
    [("oil", ["ml", "l", "l", "l"]), ("eggs", ["piece", "hali", "dozen", "tray"])],
)
async def test_unit_presets_per_base_unit(
    catalog_client: httpx.AsyncClient, product_id: str, units: list[str]
) -> None:
    products = {p["id"]: p for p in (await catalog_client.get("/v1/products")).json()}
    assert [preset["u"] for preset in products[product_id]["unit_presets"]] == units


async def test_areas(catalog_client: httpx.AsyncClient) -> None:
    response = await catalog_client.get("/v1/areas")
    assert response.json() == [
        {"id": "demo-central", "name": "Demo Central", "timezone": "Asia/Dhaka", "center": CENTER}
    ]


async def test_shops_without_point_sorted_by_name(catalog_client: httpx.AsyncClient) -> None:
    shops = (await catalog_client.get("/v1/shops")).json()
    assert [s["id"] for s in shops] == ["shop-a", "shop-b", "shop-c", "shop-d", "shop-e"]
    assert all(s["distance_m"] is None for s in shops)


async def test_shops_near_point_within_radius_sorted_by_distance(
    catalog_client: httpx.AsyncClient,
) -> None:
    response = await catalog_client.get("/v1/shops", params={**CENTER, "radius_m": 1000})
    shops = response.json()
    assert [s["id"] for s in shops] == ["shop-a", "shop-b", "shop-c"]
    distances = [s["distance_m"] for s in shops]
    assert distances == sorted(distances)
    assert all(d <= 1000 for d in distances)
    assert distances[0] == pytest.approx(300, abs=1)


async def test_shops_default_radius_is_2000m(catalog_client: httpx.AsyncClient) -> None:
    shops = (await catalog_client.get("/v1/shops", params=CENTER)).json()
    assert len(shops) == 5


async def test_shops_area_filter(catalog_client: httpx.AsyncClient) -> None:
    response = await catalog_client.get("/v1/shops", params={"area": "demo-central"})
    assert len(response.json()) == 5


async def test_shops_unknown_area_is_404(catalog_client: httpx.AsyncClient) -> None:
    response = await catalog_client.get("/v1/shops", params={"area": "atlantis"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 23.78},
        {"lng": 90.4},
        {"lat": 91, "lng": 90.4},
        {**CENTER, "radius_m": 0},
        {**CENTER, "radius_m": 50_001},
    ],
)
async def test_shops_invalid_query_is_422(
    catalog_client: httpx.AsyncClient, params: dict[str, float]
) -> None:
    response = await catalog_client.get("/v1/shops", params=params)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_catalog_unavailable_without_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/products")
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"


async def test_nearby_unavailable_without_geo_index(settings: Settings) -> None:
    app = create_app(container=memory_container(settings, geo=False))
    async for client in client_for(app):
        assert (await client.get("/v1/shops")).status_code == 200
        response = await client.get("/v1/shops", params=CENTER)
    assert response.status_code == 503
