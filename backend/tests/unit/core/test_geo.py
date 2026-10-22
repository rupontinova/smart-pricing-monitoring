import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from spm.core.geo import EARTH_RADIUS_M, haversine_m
from spm.core.models import GeoPoint

points = st.builds(
    GeoPoint,
    lat=st.floats(min_value=-90, max_value=90, allow_nan=False),
    lng=st.floats(min_value=-180, max_value=180, allow_nan=False),
)


def test_haversine_one_km_along_meridian() -> None:
    a = GeoPoint(23.8103, 90.4125)
    b = GeoPoint(a.lat + math.degrees(1000 / EARTH_RADIUS_M), a.lng)
    assert haversine_m(a, b) == pytest.approx(1000, abs=1)


def test_haversine_known_city_pair() -> None:
    # Dhaka -> Chattogram, about 213 km great-circle.
    dhaka, ctg = GeoPoint(23.8103, 90.4125), GeoPoint(22.3569, 91.7832)
    assert haversine_m(dhaka, ctg) == pytest.approx(213_000, rel=0.02)


def test_haversine_zero_for_same_point() -> None:
    p = GeoPoint(23.8103, 90.4125)
    assert haversine_m(p, p) == 0.0


@given(points, points)
def test_haversine_symmetric_and_bounded(a: GeoPoint, b: GeoPoint) -> None:
    d = haversine_m(a, b)
    assert d == pytest.approx(haversine_m(b, a), abs=1e-6)
    assert 0.0 <= d <= math.pi * EARTH_RADIUS_M + 1e-6
