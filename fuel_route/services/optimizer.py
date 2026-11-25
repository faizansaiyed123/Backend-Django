"""Fuel-stop optimization: cost-optimal refueling along a route.

Strategy (greedy, near-optimal and easy to verify):
  * The vehicle starts with a full tank (500 miles @ 10 MPG = 50 gallons).
  * Repeatedly drive as far as possible; among stations reachable before the
    tank runs dry, stop at the cheapest one.
  * Buy just enough fuel to reach the destination if it is within range after
    the stop; otherwise fill the tank completely so the next leg has maximum
    range.
  * Pulling off the route costs detour miles (there and back), which are
    charged against the tank.
"""

from heapq import heappush, heappop

from fuel_route.models import FuelStation
from routeplanner.settings import (
    ROUTE_SAMPLE_INTERVAL_MILES,
    STATION_SEARCH_RADIUS_MILES,
    VEHICLE_MAX_RANGE_MILES,
    VEHICLE_MPG,
)

from .distance import haversine_miles


class RouteSegment:
    __slots__ = ('lat', 'lon', 'mile')

    def __init__(self, lat, lon, mile):
        self.lat = lat
        self.lon = lon
        self.mile = mile


def build_route_segments(geometry, total_distance_miles):
    """Turn OSRM geometry ([lat, lon] pairs) into sampled route segments
    with cumulative mile markers."""
    if not geometry or len(geometry) < 2:
        return []

    segments = [RouteSegment(geometry[0][0], geometry[0][1], 0.0)]
    accumulated = 0.0
    for i in range(1, len(geometry)):
        lat1, lon1 = geometry[i - 1]
        lat2, lon2 = geometry[i]
        accumulated += haversine_miles(lat1, lon1, lat2, lon2)
        segments.append(RouteSegment(lat2, lon2, accumulated))

    # Normalize so the last segment lands exactly on the total distance.
    if segments:
        last = segments[-1]
        if total_distance_miles > 0:
            scale = total_distance_miles / last.mile if last.mile > 0 else 1.0
            for seg in segments:
                seg.mile *= scale
    return segments


def mile_at(segments, fraction_of_route):
    """Mile marker at a fraction (0..1) along the route (linear interp)."""
    if not segments:
        return 0.0
    total = segments[-1].mile
    target = total * fraction_of_route
    for seg in segments:
        if seg.mile >= target:
            return seg.mile
    return total


def find_stations_near_route(segments, total_distance_miles):
    """Return geocoded stations near the route with their route mile + detour.

    Efficiency: the database filters to the route's bounding box first, so we
    never scan stations that cannot possibly be near the route.
    """
    if not segments:
        return []

    lats = [s.lat for s in segments]
    lons = [s.lon for s in segments]
    margin = STATION_SEARCH_RADIUS_MILES / 69.0  # rough degrees per mile (lat)
    lon_margin = STATION_SEARCH_RADIUS_MILES / (69.0 * max(0.6, __import__('math').cos(__import__('math').radians(sum(lats) / len(lats)))))

    candidates = FuelStation.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min(lats) - margin,
        latitude__lte=max(lats) + margin,
        longitude__gte=min(lons) - lon_margin,
        longitude__lte=max(lons) + lon_margin,
    ).only('opis_id', 'name', 'address', 'city', 'state', 'retail_price', 'latitude', 'longitude')

    # Sample route points so the per-station nearest-point search is cheap.
    sample_miles = []
    point = 0.0
    while point <= total_distance_miles:
        sample_miles.append(point)
        point += ROUTE_SAMPLE_INTERVAL_MILES
    if not sample_miles or sample_miles[-1] < total_distance_miles:
        sample_miles.append(total_distance_miles)

    # Coordinates of sample points.
    sample_points = []
    for mile in sample_miles:
        seg = _segment_at(segments, mile)
        if seg:
            sample_points.append((seg.lat, seg.lon))

    stations = []
    for station in candidates:
        best_mile = None
        best_detour = float('inf')
        for (sample_mile, (plat, plon)) in zip(sample_miles, sample_points):
            detour = haversine_miles(station.latitude, station.longitude, plat, plon)
            if detour < best_detour:
                best_detour = detour
                best_mile = sample_mile
        if best_detour <= STATION_SEARCH_RADIUS_MILES and best_mile is not None:
            stations.append({
                'station': station,
                'mile': best_mile,
                'detour': best_detour,
            })
    stations.sort(key=lambda s: s['mile'])
    return stations


def _segment_at(segments, mile):
    if not segments:
        return None
    best = segments[0]
    for seg in segments:
        if abs(seg.mile - mile) < abs(best.mile - mile):
            best = seg
    return best


def optimize(segments, total_distance_miles, stations_near_route):
    """Compute the cost-optimal sequence of fuel stops.

    Strategy (textbook greedy):
      * Start with a full tank. At the current position, look at stations
        within range ahead:
        - If a cheaper station is ahead, buy just enough to reach it and
          repeat from there.
        - If no cheaper station is within range, fill the tank and drive to
          the farthest reachable station.
      * Pulling off the route costs detour miles (there and back), charged
        against the tank at each stop.
      * Only fuel purchased at stops is charged (the initial full tank is
        not part of trip spend).

    Returns (stops, total_gallons, total_cost) or None if the route cannot be
    completed within the vehicle range.
    """
    max_range = VEHICLE_MAX_RANGE_MILES
    mpg = VEHICLE_MPG
    capacity_gallons = max_range / mpg

    if total_distance_miles <= max_range:
        return ([], 0.0, 0.0)

    stations = sorted(stations_near_route, key=lambda s: s['mile'])

    stops = []
    position = 0.0
    fuel = capacity_gallons  # gallons in tank, starts full
    current_price = float('inf')  # start location is not a station
    current_station = None
    current_detour = 0.0
    total_gallons = 0.0
    total_cost = 0.0

    def record_stop(buy_gallons):
        nonlocal total_gallons, total_cost
        fuel_l = fuel + buy_gallons
        total_gallons += buy_gallons
        total_cost += buy_gallons * current_price
        stops.append({
            'opis_id': current_station.opis_id,
            'name': current_station.name,
            'address': current_station.address,
            'city': current_station.city,
            'state': current_station.state,
            'retail_price': float(current_station.retail_price),
            'mile_marker': round(position, 2),
            'detour_miles': round(current_detour, 2),
            'gallons': round(buy_gallons, 4),
            'cost': round(buy_gallons * float(current_station.retail_price), 2),
        })
        return fuel_l

    while True:
        remaining = total_distance_miles - position
        reach_miles = fuel * mpg

        # Destination within range from current position: buy just enough
        # at the current station to finish, then we are done.
        if remaining <= reach_miles:
            if current_price != float('inf'):
                buy = max(remaining / mpg - fuel, 0.0)
                if buy > 0:
                    fuel = record_stop(buy)
            break

        # A station is reachable if route miles there + detour (there and back)
        # fit within the current fuel range.
        ahead = [
            s for s in stations
            if position < s['mile']
            and (s['mile'] - position) + 2.0 * s['detour'] <= reach_miles
        ]
        if not ahead and position == 0.0:
            # At the start, stations at mile 0 are reachable with zero route driving;
            # only the detour fuel matters.
            ahead = [
                s for s in stations
                if 0.0 <= s['mile'] and 2.0 * s['detour'] <= reach_miles
            ]

        if not ahead:
            # Cannot reach any station or the destination. If we are sitting
            # at a station, fill up and re-evaluate (handles arriving with an
            # empty tank exactly at a range boundary).
            if current_price != float('inf'):
                buy = capacity_gallons - fuel
                if buy > 0:
                    fuel = record_stop(buy)
                    continue
                # Already full and still no station ahead -> stranded.
            return None

        cheaper = [s for s in ahead if float(s['station'].retail_price) < current_price]

        if cheaper:
            # Cheapest station ahead (farthest break ties).
            target = min(
                cheaper, key=lambda s: (float(s['station'].retail_price), -s['mile'])
            )
        else:
            # No cheaper station in range: fill up here, go farthest reachable.
            target = max(ahead, key=lambda s: (s['mile'], -float(s['station'].retail_price)))

        route_miles = target['mile'] - position

        # Buy fuel at the current station to reach the target.
        if current_price != float('inf'):
            if cheaper:
                buy = max(route_miles / mpg - fuel, 0.0)
            else:
                buy = capacity_gallons - fuel  # fill up
            if buy > 0:
                fuel = record_stop(buy)

        # Drive to the target, pull off-route and back (detour consumes fuel).
        fuel = fuel - route_miles / mpg
        fuel -= 2.0 * target['detour'] / mpg
        if fuel < 0:
            return None
        position = target['mile']
        current_station = target['station']
        current_detour = target['detour']
        current_price = float(target['station'].retail_price)

    return (stops, round(total_gallons, 4), round(total_cost, 2))
