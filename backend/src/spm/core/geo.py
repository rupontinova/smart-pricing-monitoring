"""Geodesy helpers."""

import math

from spm.core.models import GeoPoint

EARTH_RADIUS_M = 6_371_008.8  # mean Earth radius (IUGG)


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance between two points, in metres."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))
