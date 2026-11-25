from django.test import TestCase, override_settings
from django.test import Client
from unittest.mock import patch
import json

from fuel_route.services.optimizer import (
    build_route_segments,
    optimize,
    RouteSegment,
)
from fuel_route.models import FuelStation


def make_segment(lat, lon, mile):
    return RouteSegment(lat, lon, mile)


class OptimizerTests(TestCase):
    def test_short_route_no_stops(self):
        segments = [
            make_segment(40.0, -75.0, 0.0),
            make_segment(40.0, -75.0, 300.0),
        ]
        stations = []
        result = optimize(segments, 300.0, stations)
        self.assertEqual(result, ([], 0.0, 0.0))

    def test_one_stop_cheapest_in_range(self):
        # 700 mile route, one station at mile 300 (cheapest, reachable)
        segments = [make_segment(40.0, -75.0, m) for m in range(0, 701, 100)]
        stations = [
            {
                'station': FuelStation(
                    opis_id='1', name='Cheap Stop', address='A', city='C', state='PA',
                    retail_price=2.50, latitude=40.0, longitude=-75.0,
                ),
                'mile': 300.0,
                'detour': 1.0,
            },
            {
                'station': FuelStation(
                    opis_id='2', name='Expensive Stop',
                    address='B', city='D', state='PA', retail_price=4.00,
                    latitude=40.0, longitude=-75.0,
                ),
                'mile': 400.0,
                'detour': 1.0,
            },
        ]
        result = optimize(segments, 700.0, stations)
        self.assertIsNotNone(result)
        stops, gallons, cost = result
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]['name'], 'Cheap Stop')
        self.assertAlmostEqual(cost, gallons * 2.50, places=2)

    def test_multiple_stops(self):
        # 1200 mile range exceeds 500, need >= 2 stops
        segments = [make_segment(40.0, -75.0, m) for m in range(0, 1201, 100)]
        stations = []
        for mile, price in [(300, 3.0), (700, 2.0), (1000, 3.5)]:
            stations.append({
                'station': FuelStation(
                    opis_id=str(mile), name=f'S{mile}', address='A', city='C',
                    state='PA', retail_price=price, latitude=40.0, longitude=-75.0,
                ),
                'mile': float(mile),
                'detour': 0.0,
            })
        result = optimize(segments, 1200.0, stations)
        self.assertIsNotNone(result)
        stops, gallons, cost = result
        self.assertGreaterEqual(len(stops), 2)
        self.assertAlmostEqual(cost, sum(s['cost'] for s in stops), places=2)
        self.assertGreater(cost, 0)

    def test_unreachable_gap(self):
        segments = [make_segment(40.0, -75.0, m) for m in range(0, 1201, 100)]
        # station only at mile 600; gap 0->600 > 500 range
        stations = [
            {
                'station': FuelStation(
                    opis_id='1', name='Far', address='A', city='C', state='PA',
                    retail_price=3.0, latitude=40.0, longitude=-75.0,
                ),
                'mile': 600.0,
                'detour': 0.0,
            },
        ]
        result = optimize(segments, 1200.0, stations)
        self.assertIsNone(result)

    def test_station_exactly_at_range_boundary(self):
        segments = [make_segment(40.0, -75.0, m) for m in range(0, 1001, 100)]
        stations = [
            {
                'station': FuelStation(
                    opis_id='1', name='Edge', address='A', city='C', state='PA',
                    retail_price=3.0, latitude=40.0, longitude=-75.0,
                ),
                'mile': 500.0,
                'detour': 0.0,
            },
        ]
        result = optimize(segments, 1000.0, stations)
        self.assertIsNotNone(result)

    def test_build_route_segments_scales_to_total(self):
        geometry = [[40.0, -75.0], [40.0, -74.0], [40.0, -73.0]]
        segments = build_route_segments(geometry, 300.0)
        self.assertAlmostEqual(segments[-1].mile, 300.0, places=6)


class ApiValidationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_missing_fields(self):
        resp = self.client.post('/api/route/', data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('start', resp.json()['error'].lower())

    def test_invalid_json(self):
        resp = self.client.post('/api/route/', data='not json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_get_not_allowed(self):
        resp = self.client.get('/api/route/?start=x&finish=y')
        self.assertEqual(resp.status_code, 405)

    @patch('fuel_route.views.geocode_location')
    @patch('fuel_route.views.get_route')
    def test_success_response_shape(self, mock_get_route, mock_geocode):
        mock_geocode.side_effect = [(40.0, -75.0), (40.0, -74.0)]
        mock_get_route.return_value = (300.0, 18000, [[40.0, -75.0], [40.0, -74.0]])

        resp = self.client.post(
            '/api/route/',
            data=json.dumps({'start': 'Philadelphia, PA', 'finish': 'Atlantic City, NJ'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ('start', 'finish', 'route', 'fuel_stops', 'total_gallons', 'total_cost', 'vehicle'):
            self.assertIn(key, data)
        self.assertEqual(data['vehicle']['max_range_miles'], 500)
        self.assertEqual(data['vehicle']['mpg'], 10)
