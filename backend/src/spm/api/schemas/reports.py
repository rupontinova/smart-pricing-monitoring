from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from spm.core.enums import BaseUnit, Classification, ScoringState
from spm.core.models import GeoPoint, LocationFix, Reporter
from spm.services.auth_service import DeviceToken
from spm.services.report_service import ReportStatus, SubmitReport, SubmitResult


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID


class DeviceTokenOut(BaseModel):
    token: str
    reporter_id: UUID
    expires_at: datetime

    @classmethod
    def of(cls, t: DeviceToken) -> Self:
        return cls(token=t.token, reporter_id=t.reporter_id, expires_at=t.expires_at)


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100_000)


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_report_id: UUID
    product: str | None = Field(default=None, min_length=1, max_length=64)
    product_text: str | None = Field(default=None, min_length=1, max_length=100)
    shop_id: str = Field(min_length=1, max_length=64)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit: str = Field(min_length=1, max_length=20)
    observed_at: AwareDatetime
    location: LocationIn | None = None

    @model_validator(mode="after")
    def _product_given(self) -> Self:
        if not self.product and not self.product_text:
            raise ValueError("give product or product_text")
        return self

    def to_command(self, ip_hash: str | None) -> SubmitReport:
        loc = self.location
        return SubmitReport(
            client_report_id=self.client_report_id,
            product_id=self.product,
            product_text=self.product_text,
            shop_id=self.shop_id,
            price=self.price,
            quantity=self.quantity,
            unit=self.unit,
            observed_at=self.observed_at,
            location=LocationFix(GeoPoint(loc.lat, loc.lng), loc.accuracy_m) if loc else None,
            ip_hash=ip_hash,
        )


class BackfillReportIn(ReportIn):
    reporter_device_id: str = Field(min_length=1, max_length=64)


class NormalizedOut(BaseModel):
    product_id: str
    price_per_base_unit: float
    base_unit: BaseUnit
    base_quantity: float


class ReportOut(BaseModel):
    report_id: UUID
    status: ScoringState
    duplicate: bool
    normalized: NormalizedOut
    location_verified: bool
    distance_to_shop_m: float | None

    @classmethod
    def of(cls, result: SubmitResult) -> Self:
        r = result.report
        n = r.normalized
        return cls(
            report_id=r.id,
            status=result.state,
            duplicate=result.duplicate,
            normalized=NormalizedOut(
                product_id=n.product_id,
                price_per_base_unit=round(float(n.norm_price), 2),
                base_unit=n.base_unit,
                base_quantity=round(float(n.base_quantity), 4),
            ),
            location_verified=r.location_verified,
            distance_to_shop_m=(
                round(r.distance_to_shop_m, 1) if r.distance_to_shop_m is not None else None
            ),
        )


class ReportStatusOut(BaseModel):
    report_id: UUID
    status: ScoringState
    classification: Classification | None
    verified: bool

    @classmethod
    def of(cls, s: ReportStatus) -> Self:
        return cls(
            report_id=s.report_id, status=s.state, classification=s.classification,
            verified=s.verified,
        )  # fmt: skip


class MeOut(BaseModel):
    reporter_id: UUID
    trust: float
    reports_total: int
    created_at: datetime

    @classmethod
    def of(cls, r: Reporter) -> Self:
        return cls(
            reporter_id=r.id, trust=round(r.trust, 4), reports_total=r.reports_total,
            created_at=r.created_at,
        )  # fmt: skip
