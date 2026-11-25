import os
import re
import tempfile

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from fuel_route.models import FuelStation

BASE_URL = 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_gaz_place_{fips}.txt'

STATE_FIPS = {
    'AL': '01', 'AK': '02', 'AZ': '04', 'AR': '05', 'CA': '06', 'CO': '08',
    'CT': '09', 'DE': '10', 'DC': '11', 'FL': '12', 'GA': '13', 'HI': '15',
    'ID': '16', 'IL': '17', 'IN': '18', 'IA': '19', 'KS': '20', 'KY': '21',
    'LA': '22', 'ME': '23', 'MD': '24', 'MA': '25', 'MI': '26', 'MN': '27',
    'MS': '28', 'MO': '29', 'MT': '30', 'NE': '31', 'NV': '32', 'NH': '33',
    'NJ': '34', 'NM': '35', 'NY': '36', 'NC': '37', 'ND': '38', 'OH': '39',
    'OK': '40', 'OR': '41', 'PA': '42', 'RI': '44', 'SC': '45', 'SD': '46',
    'TN': '47', 'TX': '48', 'UT': '49', 'VT': '50', 'VA': '51', 'WA': '53',
    'WV': '54', 'WI': '55', 'WY': '56',
}


class Command(BaseCommand):
    help = 'Geocode remaining stations by matching city/state to US Census Gazetteer place coordinates.'

    def handle(self, *args, **options):
        coords = self._load_gazetteer()
        self.stdout.write(f'Gazetteer loaded: {len(coords)} place coordinates.')

        pending = FuelStation.objects.filter(latitude__isnull=True)
        total = pending.count()
        self.stdout.write(f'{total} stations still need geocoding.')

        updated = 0
        for station in pending:
            # Try the city as-is and stripped of common suffixes.
            lookup_keys = set()
            base = station.city.strip().lower()
            lookup_keys.add(base)
            for suffix in ('city', 'town', 'village', 'borough', 'cdp'):
                if base.endswith(' ' + suffix):
                    lookup_keys.add(base[: -len(suffix) - 1])
            latlon = None
            for key_city in lookup_keys:
                k = (key_city, station.state.strip().upper())
                if k in coords:
                    latlon = coords[k]
                    break
            if not latlon:
                continue
            station.latitude, station.longitude = latlon
            station.geocoded_at = timezone.now()
            station.save(update_fields=["latitude", "longitude", "geocoded_at"])
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(f'Geocoded {updated}/{total} stations via Gazetteer.')
        )

    def _load_gazetteer(self):
        coords = {}
        tmpdir = tempfile.mkdtemp(prefix='gazetteer_')
        for state, fips in STATE_FIPS.items():
            path = os.path.join(tmpdir, f'{fips}.txt')
            if not os.path.exists(path):
                try:
                    resp = requests.get(BASE_URL.format(fips=fips), timeout=60)
                except requests.RequestException as exc:
                    self.stdout.write(self.style.WARNING(f'{state}: download failed ({exc})'))
                    continue
                if resp.status_code != 200:
                    continue
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(resp.text)

            with open(path, encoding='utf-8') as fh:
                header = [h.strip() for h in fh.readline().rstrip('\n').split('\t')]
                try:
                    name_i = header.index('NAME')
                    usps_i = header.index('USPS')
                    lat_i = header.index('INTPTLAT')
                    lon_i = header.index('INTPTLONG')
                except ValueError:
                    continue
                for line in fh:
                    parts = line.rstrip('\n').split('\t')
                    if len(parts) <= max(name_i, usps_i, lat_i, lon_i):
                        continue
                    try:
                        lat = float(parts[lat_i])
                        lon = float(parts[lon_i])
                    except ValueError:
                        continue
                    if lat == 0 and lon == 0:
                        continue
                    name = re.sub(
                        r'\s*(city|town|village|borough|cdp|municipality)\s*$',
                        '',
                        parts[name_i],
                        flags=re.IGNORECASE,
                    ).strip()
                    key = (name.lower(), parts[usps_i].strip().upper())
                    coords.setdefault(key, (lat, lon))
        return coords
