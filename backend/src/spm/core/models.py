"""Domain entities and value objects. Immutable; all datetimes are timezone-aware UTC."""

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from spm.core.enums import AnomalyKind, BaseUnit, Classification, Confidence, Direction


@dataclass(frozen=True, slots=True)
class GeoPoint:
    lat: float
    lng: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lat) and -90.0 <= self.lat <= 90.0):
            raise ValueError(f"latitude must be within [-90, 90], got {self.lat}")
        if not (math.isfinite(self.lng) and -180.0 <= self.lng <= 180.0):
            raise ValueError(f"longitude must be within [-180, 180], got {self.lng}")


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    base_unit: BaseUnit
    category: str
    half_life_h: int
    abs_min: Decimal
    abs_max: Decimal
    active: bool = True
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.half_life_h <= 0:
            raise ValueError("half_life_h must be positive")
        if not self.abs_min < self.abs_max:
            raise ValueError("abs_min must be below abs_max")


@dataclass(frozen=True, slots=True)
class Area:
    id: str
    name: str
    timezone: str
    center: GeoPoint
    quiet_start: time
    quiet_end: time


@dataclass(frozen=True, slots=True)
class Shop:
    id: str
    name: str
    area_id: str
    location: GeoPoint
    address: str = ""
    active: bool = True


@dataclass(frozen=True, slots=True)
class Reporter:
    id: UUID
    device_id: str
    alpha: float
    beta: float
    reports_total: int
    banned: bool
    created_at: datetime
    last_seen_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.alpha <= 0 or self.beta <= 0:
            raise ValueError("Beta reputation parameters must be positive")

    @property
    def trust(self) -> float:
        """Posterior mean of the Beta(α, β) reputation."""
        return self.alpha / (self.alpha + self.beta)


@dataclass(frozen=True, slots=True)
class RawPriceInput:
    product_id: str | None
    product_text: str | None
    price: Decimal
    quantity: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class NormalizedPrice:
    product_id: str
    base_unit: BaseUnit
    base_quantity: Decimal
    norm_price: Decimal
    raw: RawPriceInput


@dataclass(frozen=True, slots=True)
class LocationFix:
    point: GeoPoint
    accuracy_m: float | None = None


@dataclass(frozen=True, slots=True)
class Report:
    id: UUID
    received_at: datetime
    reporter_id: UUID
    client_report_id: UUID
    product_id: str
    shop_id: str
    area_id: str
    normalized: NormalizedPrice
    observed_at: datetime
    location: LocationFix | None
    distance_to_shop_m: float | None
    location_verified: bool
    ip_hash: str | None


@dataclass(frozen=True, slots=True)
class Observation:
    """The scoring view of a report."""

    report_id: UUID
    reporter_id: UUID
    shop_id: str
    norm_price: float
    observed_at: datetime
    trust: float
    location_verified: bool
    classification: Classification | None
    ip_hash: str | None


@dataclass(frozen=True, slots=True)
class Assessment:
    report_id: UUID
    classification: Classification
    direction: Direction | None
    z_shop: float | None
    z_market: float | None
    reference_price: float | None
    insufficient_data: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Anomaly:
    id: UUID
    kind: AnomalyKind
    direction: Direction
    product_id: str
    area_id: str
    shop_id: str | None
    observed_price: float
    reference_price: float
    magnitude_pct: float
    report_ids: tuple[UUID, ...]
    detected_at: datetime


@dataclass(frozen=True, slots=True)
class FairBand:
    """The fair band for one product in one area. Quantiles are None when there is no data."""

    product_id: str
    area_id: str
    p25: float | None
    p50: float | None
    p75: float | None
    mad: float | None
    total_weight: float
    n_eff: float
    n_shops: int
    confidence: Confidence
    newest_obs_at: datetime | None
    mean_age_h: float | None
    half_life_h: int
    rebase_at: datetime | None
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class ShopPrice:
    product_id: str
    shop_id: str
    area_id: str
    price: float
    verified: bool
    support_weight: float
    n_reports: int
    last_observed_at: datetime
    verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class UnitHint:
    message: str
    suggested_quantity: Decimal
    suggested_unit: str
    resulting_norm_price: Decimal


@dataclass(frozen=True, slots=True)
class DailyStat:
    """One day's rollup for a product in an area (``shop_id=None``) or at one shop (M17)."""

    product_id: str
    area_id: str
    shop_id: str | None
    day: date
    p25: float
    p50: float
    p75: float
    mad: float
    n_reports: int
    total_weight: float
    final: bool
