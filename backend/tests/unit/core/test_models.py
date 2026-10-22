from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from spm.core.enums import BaseUnit, Classification, Confidence, Verdict
from spm.core.models import GeoPoint, Product, Reporter


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(90.0001, 0.0), (-90.0001, 0.0), (0.0, 180.0001), (0.0, -180.0001), (float("nan"), 0.0)],
)
def test_geopoint_rejects_out_of_range(lat: float, lng: float) -> None:
    with pytest.raises(ValueError):
        GeoPoint(lat, lng)


def test_geopoint_accepts_boundaries() -> None:
    assert GeoPoint(90.0, 180.0) == GeoPoint(90.0, 180.0)
    assert GeoPoint(-90.0, -180.0).lat == -90.0


def test_geopoint_is_immutable() -> None:
    point = GeoPoint(23.81, 90.41)
    with pytest.raises(AttributeError):
        point.lat = 0.0  # type: ignore[misc]


def _reporter(alpha: float, beta: float) -> Reporter:
    return Reporter(
        id=UUID(int=1),
        device_id="dev-1",
        alpha=alpha,
        beta=beta,
        reports_total=0,
        banned=False,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(("alpha", "beta"), [(1.0, 1.0), (9.0, 1.0), (2.5, 7.5)])
def test_reporter_trust_is_beta_mean(alpha: float, beta: float) -> None:
    assert _reporter(alpha, beta).trust == pytest.approx(alpha / (alpha + beta))


def test_reporter_rejects_non_positive_beta_params() -> None:
    with pytest.raises(ValueError):
        _reporter(0.0, 1.0)


def test_product_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError):
        Product("onion", "Onion", BaseUnit.KG, "veg", 168, Decimal("500"), Decimal("10"))


def test_enum_values_match_storage_and_api() -> None:
    assert BaseUnit.KG.value == "kg"
    assert Classification.SHOP_OUTLIER.value == "SHOP_OUTLIER"
    assert Confidence.STALE.value == "stale"
    assert Verdict.GOOD_DEAL.value == "GOOD_DEAL"
