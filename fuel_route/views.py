"""API views for the fuel route planner."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

import json

from fuel_route.services.geocode import geocode_location
from fuel_route.services.routing import get_route
from fuel_route.services.optimizer import build_route_segments, find_stations_near_route, optimize
from routeplanner.settings import VEHICLE_MAX_RANGE_MILES, VEHICLE_MPG


def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


@csrf_exempt
@require_http_methods(['POST'])
def route_plan(request):
    """
    Plan a fuel-optimized route between two USA locations.

    Request body (JSON):
        {
            "start": "New York, NY",
            "finish": "Los Angeles, CA"
        }

    Response:
        {
            "start": {"query": "...", "lat": ..., "lon": ...},
            "finish": {"query": "...", "lat": ..., "lon": ...},
            "route": {"distance_miles": ..., "duration_seconds": ..., "geometry": [[lat, lon], ...]},
            "fuel_stops": [...],
            "total_gallons": ...,
            "total_cost": ...,
            "vehicle": {"max_range_miles": 500, "mpg": 10}
        }
    """
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, AttributeError):
        return _error('Invalid JSON body.')

    if not isinstance(body, dict):
        return _error('Request body must be a JSON object.')

    start = body.get('start')
    finish = body.get('finish')

    if not start or not isinstance(start, str) or not start.strip():
        return _error('"start" is required and must be a non-empty string.')
    if not finish or not isinstance(finish, str) or not finish.strip():
        return _error('"finish" is required and must be a non-empty string.')

    start = start.strip()
    finish = finish.strip()

    start_coord = geocode_location(start)
    if start_coord is None:
        return _error(f'Could not geocode start location: {start}')

    finish_coord = geocode_location(finish)
    if finish_coord is None:
        return _error(f'Could not geocode finish location: {finish}')

    route = get_route(start_coord[0], start_coord[1], finish_coord[0], finish_coord[1])
    if route is None:
        return _error('Could not calculate a route between the two locations.', status=502)

    distance_miles, duration_seconds, geometry = route

    segments = build_route_segments(geometry, distance_miles)
    stations_near_route = find_stations_near_route(segments, distance_miles)
    result = optimize(segments, distance_miles, stations_near_route)

    if result is None:
        return _error(
            'Route cannot be completed with a 500-mile range: no fuel station '
            'within range along the route.',
            status=422,
        )

    stops, total_gallons, total_cost = result

    return JsonResponse({
        'start': {'query': start, 'lat': start_coord[0], 'lon': start_coord[1]},
        'finish': {'query': finish, 'lat': finish_coord[0], 'lon': finish_coord[1]},
        'route': {
            'distance_miles': round(distance_miles, 2),
            'duration_seconds': round(duration_seconds),
            'geometry': geometry,
        },
        'fuel_stops': stops,
        'total_gallons': total_gallons,
        'total_cost': total_cost,
        'vehicle': {'max_range_miles': VEHICLE_MAX_RANGE_MILES, 'mpg': VEHICLE_MPG},
    })
