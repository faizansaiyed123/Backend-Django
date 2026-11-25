"""Fuel-stop candidate discovery and cost-optimal refueling."""

from bisect import bisect_left, bisect_right
from math import cos, degrees, radians

from fuel_route.models import FuelStation
from routeplanner.settings import (
    ROUTE_SAMPLE_INTERVAL_MILES,
    STATION_SEARCH_RADIUS_MILES,
    VEHICLE_MAX_RANGE_MILES,
    VEHICLE_MPG,
)

from .distance import EARTH_RADIUS_MILES, haversine_miles


class RouteSegment:
    __slots__ = ("lat", "lon", "mile")

    def __init__(self, lat, lon, mile):
        self.lat = lat
        self.lon = lon
        self.mile = mile


def build_route_segments(geometry, total_distance_miles):
    """Build route points with cumulative route-mile positions."""
    if not geometry or len(geometry) < 2:
        return []

    segments = [RouteSegment(geometry[0][0], geometry[0][1], 0.0)]
    accumulated = 0.0

    for index in range(1, len(geometry)):
        lat1, lon1 = geometry[index - 1]
        lat2, lon2 = geometry[index]
        accumulated += haversine_miles(lat1, lon1, lat2, lon2)
        segments.append(RouteSegment(lat2, lon2, accumulated))

    if accumulated > 0 and total_distance_miles > 0:
        scale = total_distance_miles / accumulated
        for segment in segments:
            segment.mile *= scale

    return segments


def _point_at_mile(segments, markers, mile):
    """Interpolate a geographic point at a route-mile position."""
    if not segments:
        return None

    if mile <= 0:
        return segments[0].lat, segments[0].lon

    if mile >= markers[-1]:
        return segments[-1].lat, segments[-1].lon

    right = bisect_left(markers, mile)
    if right == 0:
        return segments[0].lat, segments[0].lon

    left = right - 1
    a = segments[left]
    b = segments[right]
    span = b.mile - a.mile

    if span <= 0:
        return a.lat, a.lon

    fraction = (mile - a.mile) / span
    lat = a.lat + (b.lat - a.lat) * fraction
    lon = a.lon + (b.lon - a.lon) * fraction
    return lat, lon


def _project_station_to_route(
    station_lat,
    station_lon,
    segments,
    markers,
    approx_mile,
):
    """
    Project a station onto the nearest route segment around its coarse
    candidate position.

    Candidate discovery uses route samples for speed. This pass removes
    sampling error before fuel-stop optimization.
    """
    if len(segments) < 2:
        return approx_mile, None

    window_miles = max(50.0, ROUTE_SAMPLE_INTERVAL_MILES * 2.5)
    start = max(
        0,
        bisect_left(markers, approx_mile - window_miles) - 1,
    )
    end = min(
        len(segments) - 2,
        bisect_right(markers, approx_mile + window_miles),
    )

    reference_lat = radians(station_lat)
    cosine_lat = max(cos(reference_lat), 0.1)

    def to_xy(lat, lon):
        return (
            radians(lon) * EARTH_RADIUS_MILES * cosine_lat,
            radians(lat) * EARTH_RADIUS_MILES,
        )

    px, py = to_xy(station_lat, station_lon)
    best_distance = float("inf")
    best_mile = approx_mile

    for index in range(start, end + 1):
        a = segments[index]
        b = segments[index + 1]

        ax, ay = to_xy(a.lat, a.lon)
        bx, by = to_xy(b.lat, b.lon)

        dx = bx - ax
        dy = by - ay
        length_squared = dx * dx + dy * dy

        if length_squared == 0:
            fraction = 0.0
        else:
            fraction = ((px - ax) * dx + (py - ay) * dy) / length_squared
            fraction = max(0.0, min(1.0, fraction))

        projected_x = ax + fraction * dx
        projected_y = ay + fraction * dy

        projected_lat = degrees(projected_y / EARTH_RADIUS_MILES)
        projected_lon = degrees(
            projected_x / (EARTH_RADIUS_MILES * cosine_lat)
        )

        distance = haversine_miles(
            station_lat,
            station_lon,
            projected_lat,
            projected_lon,
        )

        if distance < best_distance:
            best_distance = distance
            best_mile = a.mile + fraction * (b.mile - a.mile)

    return best_mile, best_distance


def find_stations_near_route(segments, total_distance_miles):
    """
    Find fuel stations near the route.

    The database applies a route bounding box first. Candidate stations are
    then checked against coarse route samples and finally projected onto the
    actual route geometry for accurate route-mile and detour calculations.
    """
    if not segments:
        return []

    lats = [segment.lat for segment in segments]
    lons = [segment.lon for segment in segments]

    average_lat = sum(lats) / len(lats)
    coarse_search_radius = (
        STATION_SEARCH_RADIUS_MILES + ROUTE_SAMPLE_INTERVAL_MILES / 2.0
    )
    latitude_margin = coarse_search_radius / 69.0
    longitude_margin = coarse_search_radius / (
        69.0 * max(cos(radians(average_lat)), 0.1)
    )

    candidates = (
        FuelStation.objects
        .filter(
            latitude__isnull=False,
            longitude__isnull=False,
            latitude__gte=min(lats) - latitude_margin,
            latitude__lte=max(lats) + latitude_margin,
            longitude__gte=min(lons) - longitude_margin,
            longitude__lte=max(lons) + longitude_margin,
        )
        .only(
            "opis_id",
            "name",
            "address",
            "city",
            "state",
            "retail_price",
            "latitude",
            "longitude",
        )
    )

    markers = [segment.mile for segment in segments]

    sample_miles = []
    mile = 0.0
    while mile < total_distance_miles:
        sample_miles.append(mile)
        mile += ROUTE_SAMPLE_INTERVAL_MILES
    sample_miles.append(total_distance_miles)

    sample_points = [
        _point_at_mile(segments, markers, mile)
        for mile in sample_miles
    ]

    stations = []

    for station in candidates:
        approximate_mile = None
        approximate_distance = float("inf")

        for sample_mile, sample_point in zip(sample_miles, sample_points):
            distance = haversine_miles(
                station.latitude,
                station.longitude,
                sample_point[0],
                sample_point[1],
            )
            if distance < approximate_distance:
                approximate_distance = distance
                approximate_mile = sample_mile

        if (
            approximate_mile is None
            or approximate_distance > coarse_search_radius
        ):
            continue

        route_mile, detour_miles = _project_station_to_route(
            station.latitude,
            station.longitude,
            segments,
            markers,
            approximate_mile,
        )

        if detour_miles is None or detour_miles > STATION_SEARCH_RADIUS_MILES:
            continue

        stations.append(
            {
                "station": station,
                "mile": route_mile,
                "detour": detour_miles,
            }
        )

    stations.sort(key=lambda item: item["mile"])
    return stations


def _select_target(current_price, reachable_stations):
    """
    Select the next stop using the standard greedy refueling rule.

    If a cheaper station is reachable within a full tank, use the first
    cheaper station. Otherwise fill the current tank and skip to the
    farthest reachable station.
    """
    if current_price == float("inf"):
        return max(reachable_stations, key=lambda item: item["mile"])

    cheaper = [
        item
        for item in reachable_stations
        if float(item["station"].retail_price) < current_price
    ]

    if cheaper:
        return min(cheaper, key=lambda item: item["mile"])

    return max(reachable_stations, key=lambda item: item["mile"])


def optimize(segments, total_distance_miles, stations_near_route):
    """
    Compute a cost-optimal fuel-stop sequence.

    Assumption: the vehicle starts with a full 50-gallon tank. The initial
    tank is not charged; only fuel purchased during the trip contributes to
    total_gallons and total_cost.
    """
    max_range = VEHICLE_MAX_RANGE_MILES
    mpg = VEHICLE_MPG
    capacity_gallons = max_range / mpg

    if total_distance_miles <= max_range:
        return [], 0.0, 0.0

    stations = sorted(
        stations_near_route,
        key=lambda item: item["mile"],
    )
    station_miles = [item["mile"] for item in stations]

    stops = []
    position = 0.0
    fuel = capacity_gallons
    current_price = float("inf")
    current_station = None
    current_station_detour = 0.0
    total_gallons = 0.0
    total_cost = 0.0

    def record_stop(buy_gallons):
        nonlocal total_gallons, total_cost

        price = float(current_station.retail_price)
        cost = buy_gallons * price

        total_gallons += buy_gallons
        total_cost += cost

        stops.append(
            {
                "opis_id": current_station.opis_id,
                "name": current_station.name,
                "address": current_station.address,
                "city": current_station.city,
                "state": current_station.state,
                "retail_price": price,
                "mile_marker": round(position, 2),
                "detour_miles": round(current_station_detour, 2),
                "gallons": round(buy_gallons, 4),
                "cost": round(cost, 2),
            }
        )

    while True:
        remaining = total_distance_miles - position
        current_reach = fuel * mpg

        if remaining <= current_reach + 1e-9:
            if current_station is not None:
                buy_gallons = max(remaining / mpg - fuel, 0.0)
                if buy_gallons > 1e-9:
                    fuel += buy_gallons
                    record_stop(buy_gallons)
            break

        # If the destination is within one full tank from the current
        # station, buy only what is needed to finish.
        if current_station is not None and remaining <= max_range + 1e-9:
            buy_gallons = max(remaining / mpg - fuel, 0.0)
            if buy_gallons > capacity_gallons - fuel + 1e-9:
                return None

            if buy_gallons > 1e-9:
                fuel += buy_gallons
                record_stop(buy_gallons)
            break

        # For refueling decisions, evaluate all stations reachable with a
        # full tank. This lets a cheaper station beyond current fuel reach
        # be chosen by buying the additional amount at the current station.
        decision_reach = max_range
        start_index = bisect_right(station_miles, position)
        reachable = []

        for item in stations[start_index:]:
            travel_miles = item["mile"] - position
            required_miles = travel_miles + 2.0 * item["detour"]

            if required_miles <= decision_reach + 1e-9:
                reachable.append(item)
            elif item["mile"] > position and travel_miles > decision_reach:
                break

        if not reachable:
            return None

        target = _select_target(current_price, reachable)

        travel_miles = target["mile"] - position
        required_miles = travel_miles + 2.0 * target["detour"]

        if current_station is not None:
            target_price = float(target["station"].retail_price)

            if target_price < current_price:
                buy_gallons = max(required_miles / mpg - fuel, 0.0)
            else:
                buy_gallons = capacity_gallons - fuel

            if buy_gallons > capacity_gallons - fuel + 1e-9:
                return None

            if buy_gallons > 1e-9:
                fuel += buy_gallons
                record_stop(buy_gallons)

        fuel -= required_miles / mpg

        if fuel < -1e-9:
            return None

        fuel = max(fuel, 0.0)
        position = target["mile"]
        current_station = target["station"]
        current_station_detour = target["detour"]
        current_price = float(target["station"].retail_price)

    return stops, round(total_gallons, 4), round(total_cost, 2)
