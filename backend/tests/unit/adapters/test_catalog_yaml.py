from pathlib import Path
from typing import Any

import pytest

from spm.adapters.catalog_yaml import SeedError, build_catalog, load_catalog
from spm.core.enums import BaseUnit
from spm.core.geo import haversine_m


def _area(**kw: Any) -> dict[str, Any]:
    return {
        "id": "a1",
        "name": "A1",
        "timezone": "Asia/Dhaka",
        "center": {"lat": 23.8, "lng": 90.4},
    } | kw


def _product(**kw: Any) -> dict[str, Any]:
    return {
        "id": "onion", "name": "Onion", "base_unit": "kg", "category": "veg",
        "half_life_h": 48, "abs_min": 10, "abs_max": 500, "aliases": ["piyaz"],
    } | kw  # fmt: skip


def _shop(**kw: Any) -> dict[str, Any]:
    return {"id": "s1", "name": "S1", "area_id": "a1", "location": {"lat": 23.8, "lng": 90.4}} | kw


def test_repo_seed_files_are_valid() -> None:
    catalog = load_catalog()
    assert [a.id for a in catalog.areas] == ["demo-central"]
    assert [p.id for p in catalog.products] == [
        "onion", "potato", "tomato", "rice", "lentils", "sugar", "flour", "oil", "eggs", "milk",
    ]  # fmt: skip
    assert [s.id for s in catalog.shops] == ["shop-a", "shop-b", "shop-c", "shop-d", "shop-e"]
    onion = catalog.products[0]
    assert onion.base_unit is BaseUnit.KG
    assert onion.aliases == ("peyaj", "piyaj", "piyaz", "pyaz")
    assert catalog.alias_map()["mosur dal"] == "lentils"
    assert catalog.areas[0].quiet_start.hour == 22


def test_repo_seed_shops_are_300_to_1500_m_from_area_center() -> None:
    catalog = load_catalog()
    center = catalog.areas[0].center
    distances = [haversine_m(center, s.location) for s in catalog.shops]
    assert all(299 <= d <= 1501 for d in distances), distances


def test_aliases_are_normalized() -> None:
    catalog = build_catalog([_area()], [_product(aliases=["  PIYAZ!", "Pyaz"])], [])
    assert catalog.products[0].aliases == ("piyaz", "pyaz")


@pytest.mark.parametrize(
    ("areas", "products", "shops", "message"),
    [
        ([_area(), _area()], [], [], "duplicate area ids: a1"),
        ([_area()], [_product(), _product()], [], "duplicate product ids: onion"),
        ([_area()], [], [_shop(), _shop()], "duplicate shop ids: s1"),
        ([_area()], [], [_shop(area_id="nowhere")], "unknown areas: nowhere"),
        ([_area(timezone="Mars/Olympus")], [], [], "unknown timezone"),
        ([_area()], [_product(abs_min=500, abs_max=10)], [], "abs_min"),
        ([_area()], [_product(abs_min=10, abs_max=10)], [], "abs_min"),
        ([_area()], [_product(base_unit="bag")], [], "base_unit"),
        ([_area()], [_product(half_life_h=0)], [], "half_life_h"),
        (
            [_area()],
            [_product(), _product(id="garlic", name="Garlic", aliases=["Piyaz"])],
            [],
            "collides with product 'onion'",
        ),
        (
            [_area()],
            [_product(), _product(id="garlic", name="Garlic", aliases=["onion"])],
            [],
            "collides with product 'onion'",
        ),
        ([_area()], [_product(aliases=["!!"])], [], "empty after normalization"),
        ([_area()], [_product(colour="red")], [], "colour"),
    ],
)
def test_invalid_seeds_rejected(
    areas: list[dict[str, Any]],
    products: list[dict[str, Any]],
    shops: list[dict[str, Any]],
    message: str,
) -> None:
    with pytest.raises(SeedError, match=message):
        build_catalog(areas, products, shops)


def test_missing_or_malformed_files_rejected(tmp_path: Path) -> None:
    with pytest.raises(SeedError, match=r"areas\.yaml"):
        load_catalog(tmp_path)
    (tmp_path / "areas.yaml").write_text("areas: not-a-list\n")
    with pytest.raises(SeedError, match="top-level 'areas:'"):
        load_catalog(tmp_path)
