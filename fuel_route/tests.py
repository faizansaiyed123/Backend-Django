import json
from unittest.mock import Mock, patch

from django.test import TestCase

from fuel_route.models import FuelStation, RouteCache
from fuel_route.services.geocode import geocode_location
from fuel_route.services.optimizer import (
    RouteSegment,
    _select_target,
    build_route_segments,
    optimize,
)
from fuel_route.services.routing import get_route


def make_segment(lat, lon, mile):
    return RouteSegment(lat, lon, mile)


def make_station(opis_id, name, price):
    return FuelStation(
        opis_id=str(opis_id),
        name=name,
        address="A",
        city="Test City",
        state="PA",
        retail_price=price,
        latitude=40.0,
        longitude=-75.0,
    )


def candidate(station, mile, detour=0.0):
    return {
        "station": station,
        "mile": float(mile),
        "detour": float(detour),
    }


class OptimizerTests(TestCase):
    def test_short_route_needs_no_stops(self):
        result = optimize([], 300.0, [])
        self.assertEqual(result, ([], 0.0, 0.0))

    def test_unreachable_first_gap_returns_none(self):
        station = make_station("1", "Far", 3.0)
        result = optimize(
            [],
            1200.0,
            [candidate(station, 600)],
        )
        self.assertIsNone(result)

    def test_station_at_range_boundary_is_reachable(self):
        station = make_station("1", "Edge", 3.0)
        result = optimize(
            [],
            1000.0,
            [
                candidate(station, 500),
                candidate(make_station("2", "Second", 3.2), 1000),
            ],
        )
        self.assertIsNotNone(result)

    def test_nearest_cheaper_station_is_selected(self):
        current = make_station("1", "Current", 4.0)
        nearer = make_station("2", "Near cheaper", 3.0)
        farther = make_station("3", "Farther cheaper", 2.0)

        selected = _select_target(
            4.0,
            [
                candidate(nearer, 350),
                candidate(farther, 450),
            ],
        )
        self.assertEqual(selected["station"].opis_id, "2")

    def test_farthest_station_is_selected_when_no_cheaper_option_exists(self):
        current = make_station("1", "Current", 4.0)
        first = make_station("2", "First", 4.5)
        second = make_station("3", "Second", 4.25)

        selected = _select_target(
            float(current.retail_price),
            [
                candidate(first, 350),
                candidate(second, 450),
            ],
        )
        self.assertEqual(selected["station"].opis_id, "3")

    def test_cheaper_station_within_full_tank_range_is_used(self):
        """
        The cheaper station is beyond the fuel currently in the tank but
        within the 500-mile range. The optimizer buys only the extra fuel
        needed to reach it instead of filling at the expensive station.
        """
        expensive = make_station("1", "Expensive", 4.0)
        cheap = make_station("2", "Cheap", 2.0)

        result = optimize(
            [],
            800.0,
            [
                candidate(expensive, 300),
                candidate(cheap, 550),
            ],
        )

        self.assertIsNotNone(result)
        stops, gallons, cost = result
        self.assertEqual(
            [stop["opis_id"] for stop in stops],
            ["1", "2"],
        )
        self.assertAlmostEqual(gallons, 30.0, places=4)
        self.assertAlmostEqual(cost, 70.0, places=2)
        self.assertAlmostEqual(stops[0]["gallons"], 5.0, places=4)

    def test_final_stop_buys_only_what_is_needed(self):
        station = make_station("1", "Final stop", 3.0)

        result = optimize(
            [],
            600.0,
            [candidate(station, 300, detour=10)],
        )

        self.assertIsNotNone(result)
        stops, gallons, cost = result
        self.assertEqual(len(stops), 2)
        self.assertAlmostEqual(stops[-1]["gallons"], 12.0, places=4)
        self.assertAlmostEqual(gallons, 12.0, places=4)
        self.assertAlmostEqual(cost, 36.0, places=2)

    def test_detour_distance_is_charged_against_fuel(self):
        station = make_station("1", "Detour", 3.0)

        result = optimize(
            [],
            500.0,
            [candidate(station, 480, detour=10)],
        )

        self.assertIsNotNone(result)

    def test_build_route_segments_scales_to_routing_distance(self):
        geometry = [
            [40.0, -75.0],
            [40.0, -74.0],
            [40.0, -73.0],
        ]
        segments = build_route_segments(geometry, 300.0)
        self.assertAlmostEqual(segments[-1].mile, 300.0, places=6)


class ApiValidationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_missing_fields(self):
        response = self.client.post(
            "/api/route/",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("start", response.json()["error"].lower())

    def test_invalid_json(self):
        response = self.client.post(
            "/api/route/",
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_get_not_allowed(self):
        response = self.client.get("/api/route/?start=x&finish=y")
        self.assertEqual(response.status_code, 405)

    @patch("fuel_route.views.geocode_location")
    @patch("fuel_route.views.get_route")
    def test_success_response_shape(self, mock_get_route, mock_geocode):
        mock_geocode.side_effect = [
            (40.0, -75.0),
            (40.0, -74.0),
        ]
        mock_get_route.return_value = (
            300.0,
            18000,
            [
                [40.0, -75.0],
                [40.0, -74.0],
            ],
        )

        response = self.client.post(
            "/api/route/",
            data=json.dumps(
                {
                    "start": "Philadelphia, PA",
                    "finish": "Atlantic City, NJ",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        for key in (
            "start",
            "finish",
            "route",
            "fuel_stops",
            "total_gallons",
            "total_cost",
            "vehicle",
        ):
            self.assertIn(key, data)

        self.assertEqual(data["vehicle"]["max_range_miles"], 500)
        self.assertEqual(data["vehicle"]["mpg"], 10)


class GeocodeTests(TestCase):
    @patch("fuel_route.services.geocode._rate_limit")
    @patch("fuel_route.services.geocode.requests.get")
    def test_non_us_result_is_rejected(self, mock_get, _mock_rate_limit):
        response = Mock()
        response.status_code = 200
        response.json.return_value = [
            {
                "lat": "51.5074",
                "lon": "-0.1278",
                "address": {"country_code": "gb"},
            }
        ]
        mock_get.return_value = response

        self.assertIsNone(geocode_location("London, UK"))

    @patch("fuel_route.services.geocode._rate_limit")
    @patch("fuel_route.services.geocode.requests.get")
    def test_us_result_is_cached(self, mock_get, _mock_rate_limit):
        response = Mock()
        response.status_code = 200
        response.json.return_value = [
            {
                "lat": "39.9526",
                "lon": "-75.1652",
                "address": {"country_code": "us"},
            }
        ]
        mock_get.return_value = response

        first = geocode_location("Philadelphia, PA")
        second = geocode_location(" philadelphia,   pa ")

        self.assertEqual(first, second)
        self.assertEqual(mock_get.call_count, 1)


class RoutingCacheTests(TestCase):
    @patch("fuel_route.services.routing.requests.get")
    def test_route_response_is_cached(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "routes": [
                {
                    "distance": 1609.344,
                    "duration": 3600,
                    "geometry": {
                        "coordinates": [
                            [-75.0, 40.0],
                            [-74.0, 40.0],
                        ]
                    },
                }
            ]
        }
        mock_get.return_value = response

        first = get_route(
            "Philadelphia, PA",
            40.0,
            -75.0,
            "Atlantic City, NJ",
            39.36,
            -74.42,
        )
        second = get_route(
            " philadelphia,  pa ",
            40.0,
            -75.0,
            "atlantic city, nj",
            39.36,
            -74.42,
        )

        self.assertEqual(first, second)
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(RouteCache.objects.count(), 1)
