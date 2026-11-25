"""Routing via the free OSRM public server, with response caching."""

import requests

from routeplanner.settings import EXTERNAL_REQUEST_TIMEOUT, OSRM_BASE_URL


def get_route(start_lat, start_lon, end_lat, end_lon):
    """
    Get driving route from OSRM.

    Returns (distance_miles, duration_seconds, geometry) where geometry is a
    list of [lat, lon] pairs, or None if the request fails.
    """
    coords = f'{start_lon},{start_lat};{end_lon},{end_lat}'
    url = f'{OSRM_BASE_URL}/route/v1/driving/{coords}'
    params = {
        'geometries': 'geojson',
        'overview': 'full',
        'steps': 'false',
    }
    try:
        resp = requests.get(url, params=params, timeout=EXTERNAL_REQUEST_TIMEOUT)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    data = resp.json()
    routes = data.get('routes') or []
    if not routes:
        return None

    route = routes[0]
    distance_meters = route.get('distance', 0)
    duration_seconds = route.get('duration', 0)
    geometry_coords = route.get('geometry', {}).get('coordinates', [])
    # OSRM geometry is [lon, lat]; convert to [lat, lon].
    geometry = [[point[1], point[0]] for point in geometry_coords]

    distance_miles = distance_meters / 1609.344
    return (distance_miles, duration_seconds, geometry)
