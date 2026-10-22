"""Read-only catalog endpoints (no auth)."""

from typing import Annotated

from fastapi import APIRouter, Query

from spm.api.deps import CatalogServiceDep
from spm.api.schemas.catalog import AreaOut, ProductOut, ShopOut
from spm.constants import NEARBY_RADIUS_M
from spm.core.errors import ValidationError
from spm.core.models import GeoPoint

router = APIRouter(prefix="/v1", tags=["catalog"])

MAX_RADIUS_M = 50_000


@router.get("/products")
async def list_products(service: CatalogServiceDep) -> list[ProductOut]:
    return [ProductOut.of(p) for p in service.products()]


@router.get("/areas")
async def list_areas(service: CatalogServiceDep) -> list[AreaOut]:
    return [AreaOut.of(a) for a in service.areas()]


@router.get("/shops")
async def list_shops(
    service: CatalogServiceDep,
    area: Annotated[str | None, Query(description="Only shops in this area.")] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[float, Query(gt=0, le=MAX_RADIUS_M)] = NEARBY_RADIUS_M,
) -> list[ShopOut]:
    """Active shops. With ``lat``/``lng``: within ``radius_m``, nearest first; otherwise by name."""
    if (lat is None) != (lng is None):
        raise ValidationError("lat and lng must be given together.", fields=["lat", "lng"])
    near = GeoPoint(lat, lng) if lat is not None and lng is not None else None
    return [ShopOut.of(hit) for hit in await service.shops(area, near, radius_m)]
