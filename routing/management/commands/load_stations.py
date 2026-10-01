"""Load the fuel price CSV into the database, geocoding each stop.

    python manage.py load_stations [--csv path] [--geocode-missing]

Geocoding is done once, here, never at request time:
  1. Offline: city + state is matched against the US Census Gazetteer (~96% of rows).
  2. Cached:  small towns/hamlets not in the Gazetteer are read from
              data/geocode_cache.json, which is committed to the repo.
  3. Online (opt-in, --geocode-missing): anything still unknown is geocoded
              through OpenRouteService once and added to that cache file.
"""

import csv
import json
import time
from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import FuelStation
from routing.services import ors
from routing.services.places import get_place_index
from routing.services.station_index import get_station_index

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
GEOCODE_CACHE_PATH = settings.BASE_DIR / "data" / "geocode_cache.json"
ORS_MIN_INTERVAL_SECONDS = 0.7  # free tier allows ~100 geocoding requests/minute


class Command(BaseCommand):
    help = "Load and geocode truck stop fuel prices from the provided CSV."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.FUEL_PRICES_PATH))
        parser.add_argument(
            "--geocode-missing",
            action="store_true",
            help="Geocode towns missing from the Gazetteer via OpenRouteService (needs ORS_API_KEY).",
        )

    def handle(self, *args, **options):
        with open(options["csv"], encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        skipped = Counter()
        us_rows = []
        for row in rows:
            row["State"] = row["State"].strip().upper()
            row["City"] = row["City"].strip()
            if row["State"] in US_STATES:
                us_rows.append(row)
            else:
                skipped["outside USA"] += 1

        coords = self._geocode_cities({(r["City"], r["State"]) for r in us_rows}, options["geocode_missing"])

        stations = {}  # opis_id -> FuelStation; duplicate IDs keep the lowest price
        for row in us_rows:
            point = coords.get((row["City"], row["State"]))
            if point is None:
                skipped["location unknown"] += 1
                continue
            opis_id = int(row["OPIS Truckstop ID"])
            price = Decimal(row["Retail Price"].strip())
            if opis_id in stations:
                skipped["duplicate id"] += 1
                if stations[opis_id].price <= price:
                    continue
            stations[opis_id] = FuelStation(
                opis_id=opis_id,
                name=row["Truckstop Name"].strip(),
                address=row["Address"].strip(),
                city=row["City"],
                state=row["State"],
                rack_id=int(row["Rack ID"]) if row["Rack ID"].strip() else None,
                price=price,
                latitude=point[0],
                longitude=point[1],
            )

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations.values(), batch_size=1000)
        get_station_index.cache_clear()

        self.stdout.write(self.style.SUCCESS(f"Loaded {len(stations)} fuel stations."))
        for reason, count in skipped.most_common():
            self.stdout.write(f"  skipped {count:>5}  ({reason})")

    def _geocode_cities(self, cities, allow_online):
        """Map each (city, state) to (lat, lon), or leave it out if it can't be found."""
        places = get_place_index()
        cache = json.loads(GEOCODE_CACHE_PATH.read_text()) if GEOCODE_CACHE_PATH.exists() else {}
        coords, missing = {}, []
        for city, state in sorted(cities):
            if place := places.lookup(city, state):
                coords[(city, state)] = (place.lat, place.lon)
            elif (cached := cache.get(f"{city}|{state}")) is not None:
                coords[(city, state)] = tuple(cached)
            else:
                missing.append((city, state))

        self.stdout.write(f"{len(coords)} of {len(cities)} towns located offline; {len(missing)} not found.")
        if not missing or not allow_online:
            if missing:
                self.stdout.write("  (run with --geocode-missing to look these up via OpenRouteService)")
            return coords

        self.stdout.write(f"Geocoding {len(missing)} towns via OpenRouteService...")
        for i, (city, state) in enumerate(missing, start=1):
            time.sleep(ORS_MIN_INTERVAL_SECONDS)
            try:
                hit = ors.geocode(f"{city}, {state}")
            except ors.RoutingError as exc:
                self.stderr.write(f"  stopping early: {exc}")
                break
            # Reject results that landed in a different state (a common geocoder mistake).
            if hit and places.nearest(hit[0], hit[1])[0].state == state:
                coords[(city, state)] = (hit[0], hit[1])
                cache[f"{city}|{state}"] = [round(hit[0], 6), round(hit[1], 6)]
            if i % 25 == 0:
                self.stdout.write(f"  {i}/{len(missing)}")
        GEOCODE_CACHE_PATH.write_text(json.dumps(dict(sorted(cache.items())), indent=1))
        return coords
