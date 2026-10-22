"""Catalog seed files (``backend/seeds/*.yaml``): parsing and validation.

Used by ``spm seed`` (Postgres) and ``MemoryCatalogReader`` (tests): both see the same catalog.
"""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from spm.core.enums import BaseUnit
from spm.core.models import Area, GeoPoint, Product, Shop
from spm.core.normalizer import normalize_text

# src/spm/adapters/catalog_yaml.py -> backend/seeds
SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds"


class SeedError(ValueError):
    """The seed files are invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PointIn(_Strict):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)

    def to_point(self) -> GeoPoint:
        return GeoPoint(self.lat, self.lng)


class AreaIn(_Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    timezone: str
    center: PointIn
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class ProductIn(_Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    base_unit: BaseUnit
    category: str = Field(min_length=1)
    half_life_h: int = Field(gt=0)
    abs_min: Decimal = Field(gt=0, decimal_places=2)
    abs_max: Decimal = Field(gt=0, decimal_places=2)
    active: bool = True
    aliases: list[str] = []

    @model_validator(mode="after")
    def _bounds_ordered(self) -> Self:
        if not self.abs_min < self.abs_max:
            raise ValueError(f"abs_min ({self.abs_min}) must be below abs_max ({self.abs_max})")
        return self


class ShopIn(_Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    area_id: str
    location: PointIn
    address: str = ""
    active: bool = True


@dataclass(frozen=True, slots=True)
class SeedCatalog:
    """A validated catalog. ``products`` is in display order; aliases are normalized and sorted."""

    areas: tuple[Area, ...]
    products: tuple[Product, ...]
    shops: tuple[Shop, ...]

    def alias_map(self) -> dict[str, str]:
        return {alias: p.id for p in self.products for alias in p.aliases}


def _unique(kind: str, ids: list[str]) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SeedError(f"duplicate {kind} ids: {', '.join(dupes)}")


def build_catalog(
    areas: list[dict[str, Any]], products: list[dict[str, Any]], shops: list[dict[str, Any]]
) -> SeedCatalog:
    """Validate raw seed records and convert them to domain objects. Raises ``SeedError``."""
    try:
        area_ins = [AreaIn.model_validate(a) for a in areas]
        product_ins = [ProductIn.model_validate(p) for p in products]
        shop_ins = [ShopIn.model_validate(s) for s in shops]
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise SeedError(str(exc)) from exc

    _unique("area", [a.id for a in area_ins])
    _unique("product", [p.id for p in product_ins])
    _unique("shop", [s.id for s in shop_ins])

    area_ids = {a.id for a in area_ins}
    unknown_areas = sorted({s.area_id for s in shop_ins} - area_ids)
    if unknown_areas:
        raise SeedError(f"shops reference unknown areas: {', '.join(unknown_areas)}")

    # Aliases must resolve to exactly one product, and must not shadow another product's id/name.
    owner: dict[str, str] = {}
    for p in product_ins:
        for name in {normalize_text(p.id), normalize_text(p.name)}:
            owner.setdefault(name, p.id)
    for p in product_ins:
        for raw in p.aliases:
            alias = normalize_text(raw)
            if not alias:
                raise SeedError(f"product {p.id}: alias {raw!r} is empty after normalization")
            if owner.setdefault(alias, p.id) != p.id:
                raise SeedError(f"alias {raw!r} of {p.id} collides with product {owner[alias]!r}")

    return SeedCatalog(
        areas=tuple(
            Area(a.id, a.name, a.timezone, a.center.to_point(), a.quiet_start, a.quiet_end)
            for a in area_ins
        ),
        products=tuple(
            Product(
                id=p.id,
                name=p.name,
                base_unit=p.base_unit,
                category=p.category,
                half_life_h=p.half_life_h,
                abs_min=p.abs_min,
                abs_max=p.abs_max,
                active=p.active,
                aliases=tuple(sorted({normalize_text(a) for a in p.aliases})),
            )
            for p in product_ins
        ),
        shops=tuple(
            Shop(s.id, s.name, s.area_id, s.location.to_point(), s.address, s.active)
            for s in shop_ins
        ),
    )


def _read(path: Path, key: str) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SeedError(f"{path.name}: {exc}") from exc
    records = data.get(key) if isinstance(data, dict) else None
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise SeedError(f"{path.name}: expected a top-level '{key}:' list of mappings")
    return records


def load_catalog(seeds_dir: Path = SEEDS_DIR) -> SeedCatalog:
    """Read and validate ``areas.yaml``, ``products.yaml`` and ``shops.yaml``."""
    return build_catalog(
        _read(seeds_dir / "areas.yaml", "areas"),
        _read(seeds_dir / "products.yaml", "products"),
        _read(seeds_dir / "shops.yaml", "shops"),
    )
