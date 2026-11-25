"""Routing via OSRM with persistent response caching."""

from datetime import timedelta

import requests
from django.utils import timezone

from fuel_route.models import RouteCache
from routeplanner.settings import (
    EXTERNAL_REQUEST_TIMEOUT,
    OSRM_BASE_URL,
    ROUTE_CACHE_TTL_SECONDS,
)


def _cache_key(value):
    return " ".join(value.strip().lower().split())


def get_route(start_query, start_lat, start_lon, finish_query, end_lat, end_lon):
    """
    Return (distance_miles, duration_seconds, geometry).

    Identical trips are served from RouteCache for the configured TTL,
    eliminating repeated OSRM requests.
    """
    start_key = _cache_key(start_query)
    finish_key = _cache_key(finish_query)

    cutoff = timezone.now() - timedelta(seconds=ROUTE_CACHE_TTL_SECONDS)
    cached = (
        RouteCache.objects
        .filter(
            start_query=start_key,
            finish_query=finish_key,
            created_at__gte=cutoff,
        )
        .first()
    )

    if cached:
        return (
            cached.distance_miles,
            cached.duration_seconds,
            cached.geometry,
        )

    coords = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coords}"

    try:
        response = requests.get(
            url,
            params={
                "geometries": "geojson",
                "overview": "full",
                "steps": "false",
            },
            timeout=EXTERNAL_REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    data = response.json()
    routes = data.get("routes") or []
    if not routes:
        return None

    route = routes[0]
    distance_meters = float(route.get("distance") or 0)
    duration_seconds = float(route.get("duration") or 0)
    coordinates = route.get("geometry", {}).get("coordinates", [])

    if len(coordinates) < 2:
        return None

    geometry = [
        [point[1], point[0]]
        for point in coordinates
        if len(point) >= 2
    ]

    if len(geometry) < 2:
        return None

    distance_miles = distance_meters / 1609.344

    RouteCache.objects.update_or_create(
        start_query=start_key,
        finish_query=finish_key,
        defaults={
            "start_lat": start_lat,
            "start_lon": start_lon,
            "finish_lat": end_lat,
            "finish_lon": end_lon,
            "distance_miles": distance_miles,
            "duration_seconds": duration_seconds,
            "geometry": geometry,
            "created_at": timezone.now(),
        },
    )

    return distance_miles, duration_seconds, geometry
