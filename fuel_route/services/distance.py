"""Haversine distance helpers (miles)."""

from math import radians, sin, cos, asin, sqrt

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points in miles."""
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * asin(sqrt(a))


def interpolate_point(p1, p2, fraction):
    """Linear interpolation between two (lat, lon) points."""
    lat1, lon1 = p1
    lat2, lon2 = p2
    return (lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction)
