"""US-only geocoding for trip origin and destination."""

import re
import threading
import time

import requests

from fuel_route.models import GeocodeCache
from routeplanner.settings import (
    EXTERNAL_REQUEST_TIMEOUT,
    NOMINATIM_BASE_URL,
    NOMINATIM_REQUEST_DELAY_SECONDS,
    NOMINATIM_USER_AGENT,
)


_last_call = 0.0
_lock = threading.Lock()


def normalize_location_query(query):
    """Normalize user input so equivalent locations share one cache entry."""
    return re.sub(r"\s+", " ", query.strip())


def _rate_limit():
    global _last_call

    with _lock:
        now = time.time()
        elapsed = now - _last_call
        if elapsed < NOMINATIM_REQUEST_DELAY_SECONDS:
            time.sleep(NOMINATIM_REQUEST_DELAY_SECONDS - elapsed)
        _last_call = time.time()


def geocode_location(query):
    """Return (lat, lon) only when Nominatim resolves the location in the US."""
    query = normalize_location_query(query)
    if not query:
        return None

    cache_key = query.lower()
    cached = GeocodeCache.objects.filter(query=cache_key).first()
    if cached:
        return cached.latitude, cached.longitude

    _rate_limit()

    try:
        response = requests.get(
            f"{NOMINATIM_BASE_URL}/search",
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "us",
                "addressdetails": 1,
            },
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=EXTERNAL_REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    results = response.json()
    if not results:
        return None

    result = results[0]
    address = result.get("address") or {}
    if address.get("country_code", "").lower() != "us":
        return None

    latitude = float(result["lat"])
    longitude = float(result["lon"])

    GeocodeCache.objects.update_or_create(
        query=cache_key,
        defaults={
            "latitude": latitude,
            "longitude": longitude,
            "source": "nominatim",
        },
    )

    return latitude, longitude
