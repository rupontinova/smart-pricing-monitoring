from pydantic import BaseModel

from spm.core.enums import BaseUnit
from spm.core.models import Area, GeoPoint, Product
from spm.services.catalog import ShopHit


class UnitPreset(BaseModel):
    q: int
    u: str


# Quick-pick quantities shown by the PWA for each base unit (M04).
UNIT_PRESETS: dict[BaseUnit, list[UnitPreset]] = {
    BaseUnit.KG: [
        UnitPreset(q=250, u="g"),
        UnitPreset(q=500, u="g"),
        UnitPreset(q=1, u="kg"),
        UnitPreset(q=2, u="kg"),
        UnitPreset(q=5, u="kg"),
    ],
    BaseUnit.L: [
        UnitPreset(q=500, u="ml"),
        UnitPreset(q=1, u="l"),
        UnitPreset(q=2, u="l"),
        UnitPreset(q=5, u="l"),
    ],
    BaseUnit.DOZEN: [
        UnitPreset(q=1, u="piece"),
        UnitPreset(q=1, u="hali"),
        UnitPreset(q=1, u="dozen"),
        UnitPreset(q=1, u="tray"),
    ],
}


class PointOut(BaseModel):
    lat: float
    lng: float

    @classmethod
    def of(cls, p: GeoPoint) -> "PointOut":
        return cls(lat=p.lat, lng=p.lng)


class ProductOut(BaseModel):
    id: str
    name: str
    base_unit: BaseUnit
    category: str
    aliases: list[str]
    unit_presets: list[UnitPreset]

    @classmethod
    def of(cls, p: Product) -> "ProductOut":
        return cls(
            id=p.id,
            name=p.name,
            base_unit=p.base_unit,
            category=p.category,
            aliases=list(p.aliases),
            unit_presets=UNIT_PRESETS[p.base_unit],
        )


class AreaOut(BaseModel):
    id: str
    name: str
    timezone: str
    center: PointOut

    @classmethod
    def of(cls, a: Area) -> "AreaOut":
        return cls(id=a.id, name=a.name, timezone=a.timezone, center=PointOut.of(a.center))


class ShopOut(BaseModel):
    id: str
    name: str
    area_id: str
    location: PointOut
    address: str
    distance_m: float | None

    @classmethod
    def of(cls, hit: ShopHit) -> "ShopOut":
        s = hit.shop
        return cls(
            id=s.id,
            name=s.name,
            area_id=s.area_id,
            location=PointOut.of(s.location),
            address=s.address,
            distance_m=round(hit.distance_m, 1) if hit.distance_m is not None else None,
        )
