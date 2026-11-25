import io
import csv
import time

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from fuel_route.models import FuelStation, GeocodeCache
from routeplanner.settings import (
    CENSUS_BENCHMARK,
    CENSUS_GEOCODER_URL as CENSUS_BATCH_URL,
    EXTERNAL_REQUEST_TIMEOUT,
    NOMINATIM_BASE_URL,
    NOMINATIM_REQUEST_DELAY_SECONDS,
    NOMINATIM_USER_AGENT,
)

FALLBACK_STREET = '100 Main St'
BATCH_SIZE = 2000
CENSUS_REQUEST_DELAY = 1.0


class Command(BaseCommand):
    help = 'Geocode fuel stations using the free US Census Geocoder, with Nominatim city/state fallback.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Max number of stations to geocode (0 = all pending).',
        )
        parser.add_argument(
            '--nominatim',
            action='store_true',
            help='After Census, geocode remaining stations via Nominatim city/state lookups (rate-limited).',
        )

    def handle(self, *args, **options):
        total_done = self._census_phase(options['limit'])
        if options['nominatim']:
            total_done += self._nominatim_phase(options['limit'])
        self.stdout.write(self.style.SUCCESS(f'Geocoding pass complete. {total_done} stations geocoded this run.'))

    def _pending(self, limit):
        qs = FuelStation.objects.filter(latitude__isnull=True)
        if limit:
            qs = qs[:limit]
        return list(qs)

    def _census_phase(self, limit):
        pending = self._pending(limit)
        self.stdout.write(f'{len(pending)} stations need geocoding (Census phase).')
        if not pending:
            return 0

        total_done = 0
        for offset in range(0, len(pending), BATCH_SIZE):
            chunk = pending[offset : offset + BATCH_SIZE]
            results = self._geocode_batch(chunk)
            applied = 0
            for station in chunk:
                coord = results.get(station.id)
                if coord:
                    station.latitude, station.longitude = coord
                    station.geocoded_at = timezone.now()
                    station.save(update_fields=["latitude", "longitude", "geocoded_at"])
                    applied += 1
            total_done += applied
            self.stdout.write(
                f'Census batch {offset // BATCH_SIZE + 1}: geocoded {applied}/{len(chunk)} '
                f'(running total {total_done}).'
            )
            if offset + BATCH_SIZE < len(pending):
                time.sleep(CENSUS_REQUEST_DELAY)
        return total_done

    def _nominatim_phase(self, limit):
        pending = self._pending(limit)
        self.stdout.write(f'{len(pending)} stations remain (Nominatim phase).')
        if not pending:
            return 0

        combos = {}
        for station in pending:
            combos.setdefault((station.city, station.state), []).append(station)
        self.stdout.write(f'{len(combos)} unique city/state lookups needed.')

        done = 0
        for index, ((city, state), stations) in enumerate(sorted(combos.items())):
            coord = self._nominatim_geocode(city, state)
            if coord:
                for station in stations:
                    station.latitude, station.longitude = coord
                    station.geocoded_at = timezone.now()
                    station.save(update_fields=["latitude", "longitude", "geocoded_at"])
                done += len(stations)
            if index < len(combos) - 1:
                time.sleep(NOMINATIM_REQUEST_DELAY_SECONDS)
            if (index + 1) % 50 == 0:
                self.stdout.write(f'Nominatim: {index + 1}/{len(combos)} lookups, {done} stations geocoded.')
        self.stdout.write(f'Nominatim phase done: {done} stations geocoded.')
        return done

    def _geocode_batch(self, records):
        """POST records to Census addressbatch; returns {id: (lat, lon)}."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['id', 'street', 'city', 'state', 'zip'])
        for station in records:
            writer.writerow(
                [str(station.id), station.address or FALLBACK_STREET, station.city, station.state, '']
            )

        try:
            for attempt in range(3):
                try:
                    resp = requests.post(
                        CENSUS_BATCH_URL,
                        files={'addressFile': ('addresses.csv', buf.getvalue(), 'text/csv')},
                        data={'benchmark': CENSUS_BENCHMARK, 'format': 'json'},
                        timeout=60,
                    )
                    break
                except requests.RequestException as exc:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
        except requests.RequestException as exc:
            self.stdout.write(self.style.WARNING(f'Census batch request failed: {exc}'))
            return {}

        if resp.status_code != 200:
            self.stdout.write(
                self.style.WARNING(f'Census batch returned HTTP {resp.status_code}')
            )
            return {}

        out = {}
        for line in resp.text.strip().splitlines():
            if not line or line.startswith('"id"'):
                continue
            parts = list(csv.reader([line]))[0]
            if len(parts) < 7:
                continue
            rid, match, coords = parts[0], parts[2], parts[5]
            if match != 'Match' or not coords:
                continue
            try:
                lon_str, lat_str = coords.split(',')
                out[rid.strip('"')] = (float(lat_str), float(lon_str))
            except (ValueError, IndexError):
                continue
        return out

    def _nominatim_geocode(self, city, state):
        cache_key = f'{city}, {state}, USA'
        cached = GeocodeCache.objects.filter(query=cache_key).first()
        if cached:
            return (cached.latitude, cached.longitude)

        try:
            resp = requests.get(
                f'{NOMINATIM_BASE_URL}/search',
                params={'q': cache_key, 'format': 'json', 'limit': 1},
                headers={'User-Agent': NOMINATIM_USER_AGENT},
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
            query=cache_key,
            defaults={'latitude': lat, 'longitude': lon},
        )
        return (lat, lon)
