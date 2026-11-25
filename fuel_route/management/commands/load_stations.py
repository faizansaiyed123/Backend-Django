import csv

from django.core.management.base import BaseCommand
from django.db import transaction

from fuel_route.models import FuelStation
from routeplanner.settings import FUEL_PRICES_CSV_PATH, US_STATE_CODES


class Command(BaseCommand):
    help = 'Load fuel stations from the OPIS CSV, cleaning and de-duplicating records.'

    def add_arguments(self, parser):
        parser.add_argument('--csv', type=str, default=None, help='Path to the fuel prices CSV.')
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing FuelStation rows before loading.',
        )

    def handle(self, *args, **options):
        csv_path = options['csv'] or str(FUEL_PRICES_CSV_PATH)
        if options['clear']:
            FuelStation.objects.all().delete()
            self.stdout.write('Cleared existing fuel stations.')

        rows = self._read_rows(csv_path)
        cleaned = self._clean_rows(rows)
        created = self._bulk_create(cleaned)

        self.stdout.write(
            self.style.SUCCESS(
                f'Loaded {created} fuel stations '
                f'({len(rows)} raw rows, {len(cleaned)} after cleaning).'
            )
        )

    def _read_rows(self, csv_path):
        with open(csv_path, newline='', encoding='utf-8') as fh:
            return list(csv.DictReader(fh))

    def _clean_rows(self, rows):
        """Validate, trim whitespace, filter to US states, de-duplicate by OPIS id."""
        best_by_id = {}
        skipped = 0

        for row in rows:
            opis_id = (row.get('OPIS Truckstop ID') or '').strip()
            if not opis_id:
                skipped += 1
                continue

            state = (row.get('State') or '').strip().upper()
            if state not in US_STATE_CODES:
                skipped += 1
                continue

            price_raw = (row.get('Retail Price') or '').strip()
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                skipped += 1
                continue

            record = {
                'opis_id': opis_id,
                'name': (row.get('Truckstop Name') or '').strip(),
                'address': (row.get('Address') or '').strip(),
                'city': (row.get('City') or '').strip(),
                'state': state,
                'rack_id': (row.get('Rack ID') or '').strip(),
                'retail_price': price,
            }

            existing = best_by_id.get(opis_id)
            if existing is None or price < existing['retail_price']:
                best_by_id[opis_id] = record

        if skipped:
            self.stdout.write(f'Skipped {skipped} invalid / non-US rows.')
        return list(best_by_id.values())

    @transaction.atomic
    def _bulk_create(self, records):
        objs = [
            FuelStation(
                opis_id=r['opis_id'],
                name=r['name'],
                address=r['address'],
                city=r['city'],
                state=r['state'],
                rack_id=r['rack_id'],
                retail_price=r['retail_price'],
            )
            for r in records
        ]
        FuelStation.objects.bulk_create(objs, batch_size=1000)
        return len(objs)
