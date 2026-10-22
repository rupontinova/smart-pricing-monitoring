import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spm.core.errors import ObservedAtOutOfRange, PriceOutOfBounds, TooFarFromShop
from spm.core.geo import EARTH_RADIUS_M
from spm.core.guardrails import (
    bounds_reference,
    check_absolute_bounds,
    check_observed_at,
    geofence,
    is_duplicate,
    unit_error_hints,
)
from spm.core.models import GeoPoint, LocationFix, Product, RawPriceInput, Shop
from spm.core.normalizer import normalize
from tests.unit.core.catalog_fixture import CATALOG

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _product(pid: str) -> Product:
    product = CATALOG.product(pid)
    assert product is not None
    return product


def _raw(pid: str, price: str, quantity: str, unit: str) -> RawPriceInput:
    return RawPriceInput(pid, None, Decimal(price), Decimal(quantity), unit)


def _hints(
    pid: str, price: str, quantity: str, unit: str, reference: float | None = None
) -> list[str]:
    product = _product(pid)
    ref = reference if reference is not None else bounds_reference(product, None)
    return [h.message for h in unit_error_hints(_raw(pid, price, quantity, unit), product, ref)]


# ------------------------------------------------------------------------------- hints
def test_hints_onion_500_kg_suggests_500_g() -> None:
    assert _hints("onion", "40", "500", "kg") == ["Did you mean 500 g instead of 500 kg?"]


def test_hints_eggs_dozen_suggests_1_piece() -> None:
    assert "Did you mean 1 piece instead of 1 dozen?" in _hints("eggs", "12", "1", "dozen")


def test_hints_equivalent_suggestions_deduplicated() -> None:
    # "1 piece" and "0.083 dozen" give (almost) the same price: only the first is kept.
    hints = _hints("eggs", "12", "1", "dozen")
    assert len(hints) == 1


def test_hints_ranked_by_reference_and_capped_at_two() -> None:
    # 30 pieces for 330 -> candidates include 15 pieces (264/dozen) and 60 pieces (66/dozen).
    default = _hints("eggs", "330", "30", "piece")  # reference sqrt(60*400) = 155
    assert default[0] == "Did you mean 15 piece instead of 30 piece?"
    cheap_market = _hints("eggs", "330", "30", "piece", reference=70.0)
    assert cheap_market[0] == "Did you mean 60 piece instead of 30 piece?"
    assert len(default) <= 2


def test_hints_respect_bounds_and_base_quantity() -> None:
    product = _product("onion")
    for hint in unit_error_hints(_raw("onion", "40", "1", "g"), product, 100.0):
        assert product.abs_min <= hint.resulting_norm_price <= product.abs_max
    assert _hints("onion", "80000", "1", "kg") == []  # nothing plausible
    assert _hints("onion", "40", "1", "bag") == []  # unparseable unit


def test_bounds_reference_prefers_band_median() -> None:
    onion = _product("onion")
    assert bounds_reference(onion, 62.5) == 62.5
    assert bounds_reference(onion, None) == pytest.approx(math.sqrt(10 * 500))


# ------------------------------------------------------------------------------ bounds
def test_check_absolute_bounds_passes_inside() -> None:
    np = normalize(_raw("onion", "60", "1", "kg"), CATALOG)
    check_absolute_bounds(np, _product("onion"), None)


def test_check_absolute_bounds_rejects_with_hints() -> None:
    np = normalize(_raw("eggs", "12", "1", "dozen"), CATALOG)
    with pytest.raises(PriceOutOfBounds) as info:
        check_absolute_bounds(np, _product("eggs"), None)
    extra = info.value.extra
    assert extra["min"] == 60 and extra["max"] == 400
    assert extra["hints"] == [
        {"message": "Did you mean 1 piece instead of 1 dozen?", "quantity": 1.0, "unit": "piece",
         "norm_price": 144.0},
    ]  # fmt: skip
    assert "below the minimum of 60" in info.value.detail


def test_check_absolute_bounds_above_max() -> None:
    np = normalize(_raw("onion", "800", "1", "kg"), CATALOG)
    with pytest.raises(PriceOutOfBounds, match="above the maximum"):
        check_absolute_bounds(np, _product("onion"), None)


# ----------------------------------------------------------------------------- geofence
SHOP = Shop("shop-a", "Shop A", "demo-central", GeoPoint(23.7808, 90.4073))


def _fix_at(distance_m: float, accuracy_m: float | None) -> LocationFix:
    lat = SHOP.location.lat + math.degrees(distance_m / EARTH_RADIUS_M)
    return LocationFix(GeoPoint(lat, SHOP.location.lng), accuracy_m)


def test_geofence_299m_no_accuracy_ok() -> None:
    verified, distance = geofence(SHOP, _fix_at(299, None))
    assert verified and distance == pytest.approx(299, abs=0.5)


def test_geofence_450m_accuracy_200_ok() -> None:
    assert geofence(SHOP, _fix_at(450, 200))[0]


def test_geofence_520m_accuracy_500_rejected_allowance_capped() -> None:
    with pytest.raises(TooFarFromShop) as info:
        geofence(SHOP, _fix_at(520, 500))
    assert info.value.extra["allowed_m"] == 500
    assert info.value.extra["distance_m"] == pytest.approx(520, abs=0.5)


def test_geofence_no_fix_accepted_unverified() -> None:
    assert geofence(SHOP, None) == (False, None)


def test_geofence_negative_accuracy_treated_as_zero() -> None:
    with pytest.raises(TooFarFromShop):
        geofence(SHOP, _fix_at(310, -50))


# --------------------------------------------------------------------------- duplicates
@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [
        ("101.00", "100", True),  # exactly 1%
        ("99.00", "100", True),
        ("101.01", "100", False),
        ("98.99", "100", False),
        ("100", "100", True),
    ],
)
def test_is_duplicate_at_1_percent_boundary(new: str, old: str, expected: bool) -> None:
    assert is_duplicate(Decimal(new), [Decimal(old)]) is expected


def test_is_duplicate_any_of_recent() -> None:
    assert is_duplicate(Decimal(50), [Decimal(80), Decimal("50.4")])
    assert not is_duplicate(Decimal(50), [])


# -------------------------------------------------------------------------- observed_at
@pytest.mark.parametrize(
    ("offset", "ok"),
    [
        (-timedelta(days=8), False),
        (-timedelta(days=6), True),
        (-timedelta(hours=168), True),  # exactly 7 days is still allowed
        (timedelta(minutes=4), True),
        (timedelta(minutes=5), True),
        (timedelta(minutes=6), False),
    ],
)
def test_check_observed_at(offset: timedelta, ok: bool) -> None:
    if ok:
        check_observed_at(NOW + offset, NOW)
    else:
        with pytest.raises(ObservedAtOutOfRange):
            check_observed_at(NOW + offset, NOW)


def test_backfill_skips_age_check_but_not_future_check() -> None:
    check_observed_at(NOW - timedelta(days=60), NOW, allow_backfill=True)
    with pytest.raises(ObservedAtOutOfRange):
        check_observed_at(NOW + timedelta(minutes=10), NOW, allow_backfill=True)
