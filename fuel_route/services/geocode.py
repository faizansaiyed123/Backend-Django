"""Geocoding for API requests (start/finish locations) via Nominatim, cached."""

import threading
import time

import requests

from fuel_route.models import GeocodeCache
from routeplanner.settings import NOMINATIM_BASE_URL, NOMINATIM_REQUEST_DELAY_SECONDS

_last_call = 0.0
_lock = threading.Lock()


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        elapsed = now - _last_call
        if elapsed < NOMINATIM_REQUEST_DELAY_SECONDS:
            time.sleep(NOMINATIM_REQUEST_DELAY_SECONDS - elapsed)
        _last_call = time.time()


def geocode_location(query):
    """Geocode a free-text location to (lat, lon). Results are cached in the DB."""
    query = query.strip()
    if not query:
        return None

    cached = GeocodeCache.objects.filter(query=query).first()
    if cached:
        return (cached.latitude, cached.longitude)

    _rate_limit()
    try:
        resp = requests.get(
            f'{NOMINATIM_BASE_URL}/search',
            params={'q': query, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'spotter-fuel-route-assessment/1.0'},
            timeout=20,
        )
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    data = resp.json()
    if not data:
        return None

    lat = float(data[0]['lat'])
    lon = float(data[0]['lon'])
    GeocodeCache.objects.update_or_create(
        query=query,
        defaults={'latitude': lat, 'longitude': lon},
    )
    return (lat, lon)
