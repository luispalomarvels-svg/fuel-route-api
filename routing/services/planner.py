"""Turns (start, finish) into a route with an optimal fuel plan.

External API budget per uncached request:
  * 1 directions call (always)
  * +1 geocoding call per endpoint only when it isn't "lat,lon" or "City, ST"
"""

import hashlib
import re
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from . import ors
from .fuel_optimizer import plan_fuel_stops
from .places import get_place_index
from .station_index import get_station_index

# A point must be this close to a known US place to count as "in the USA".
MAX_MILES_FROM_US_PLACE = 25
CACHE_VERSION = "v1"

_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_NON_US_REGIONS = {  # Canadian provinces/territories and Mexican state codes
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
    "BCN", "CHH", "COA", "NLE", "SON", "TAM",
}
_CITY_STATE_RE = re.compile(r"^\s*([^,]+?)\s*,\s*([A-Za-z]{2})\s*(?:,\s*(?:USA?|United States))?\s*$", re.IGNORECASE)


class LocationError(Exception):
    """An input location could not be resolved or is outside the USA."""


@dataclass
class ResolvedLocation:
    query: str
    label: str
    lat: float
    lon: float
    source: str  # "coordinates" | "gazetteer" | "geocoder"

    def as_dict(self):
        return {"query": self.query, "label": self.label, "lat": round(self.lat, 6), "lon": round(self.lon, 6), "resolved_by": self.source}


def resolve_location(raw):
    """Resolve user input to coordinates, preferring offline lookups over API calls."""
    raw = (raw or "").strip()
    if not raw:
        raise LocationError("Location is empty.")

    places = get_place_index()
    region = raw.rsplit(",", 1)[-1].strip().upper() if "," in raw else ""
    if region in _NON_US_REGIONS or region in {"CANADA", "MEXICO"}:
        raise LocationError(f"'{raw}' is outside the USA.")
    if m := _LATLON_RE.match(raw):
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise LocationError(f"'{raw}' is not a valid 'lat,lon' pair.")
        loc = ResolvedLocation(raw, f"{lat:.5f},{lon:.5f}", lat, lon, "coordinates")
    elif (m := _CITY_STATE_RE.match(raw)) and (place := places.lookup(m.group(1), m.group(2))):
        loc = ResolvedLocation(raw, f"{m.group(1).title()}, {place.state}", place.lat, place.lon, "gazetteer")
    else:
        hit = ors.geocode(raw)
        if hit is None:
            raise LocationError(f"Could not find a US location matching '{raw}'.")
        loc = ResolvedLocation(raw, hit[2], hit[0], hit[1], "geocoder")

    _, miles = places.nearest(loc.lat, loc.lon)
    if miles > MAX_MILES_FROM_US_PLACE:
        raise LocationError(f"'{raw}' does not appear to be within the USA.")
    return loc


def _cache_key(*parts):
    normalized = "|".join(re.sub(r"\s+", " ", p.strip().lower()).replace(" ,", ",").replace(", ", ",") for p in parts)
    return f"route:{CACHE_VERSION}:" + hashlib.sha256(normalized.encode()).hexdigest()


def plan_route(start, finish):
    """Plan a trip. Returns a JSON-serialisable dict (also cached)."""
    started = time.perf_counter()
    key = _cache_key(start, finish)
    if (cached := cache.get(key)) is not None:
        cached["meta"] = {**cached["meta"], "cached": True, "external_api_calls": 0,
                          "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
        return cached

    origin, destination = resolve_location(start), resolve_location(finish)
    geocoding_calls = sum(loc.source == "geocoder" for loc in (origin, destination))

    route = ors.directions((origin.lat, origin.lon), (destination.lat, destination.lon))

    candidates = get_station_index().stations_along(
        route["coordinates"], settings.ROUTE_CORRIDOR_MILES, route["distance_miles"]
    )
    plan = plan_fuel_stops(
        candidates, route["distance_miles"], settings.VEHICLE_RANGE_MILES, settings.VEHICLE_MPG
    )

    result = {
        "start": origin.as_dict(),
        "finish": destination.as_dict(),
        "route": {
            "distance_miles": round(route["distance_miles"], 1),
            "duration_hours": round(route["duration_seconds"] / 3600, 2),
            "polyline": route["polyline"],
            "coordinates": route["coordinates"],
        },
        "fuel_summary": {
            "total_fuel_cost": round(plan.total_cost, 2),
            "total_gallons_purchased": round(plan.total_gallons, 2),
            "fuel_used_gallons": round(route["distance_miles"] / settings.VEHICLE_MPG, 2),
            "number_of_stops": len(plan.stops),
            "average_price_paid": round(plan.total_cost / plan.total_gallons, 3) if plan.total_gallons else None,
            "vehicle_range_miles": settings.VEHICLE_RANGE_MILES,
            "vehicle_mpg": settings.VEHICLE_MPG,
            "assumptions": "Vehicle starts with a full tank; buys only the fuel it needs; arrives empty.",
        },
        "fuel_stops": [
            {
                "stop": i,
                "opis_id": s.route_station.station.opis_id,
                "name": s.route_station.station.name,
                "address": s.route_station.station.address,
                "city": s.route_station.station.city,
                "state": s.route_station.station.state,
                "lat": s.route_station.station.lat,
                "lon": s.route_station.station.lon,
                "mile_marker": round(s.route_station.mile, 1),
                "off_route_miles": round(s.route_station.off_route_miles, 1),
                "price_per_gallon": round(s.route_station.station.price, 3),
                "gallons": round(s.gallons, 2),
                "cost": round(s.cost, 2),
            }
            for i, s in enumerate(plan.stops, start=1)
        ],
        "meta": {
            "cached": False,
            "external_api_calls": 1 + geocoding_calls,
            "stations_considered": len(candidates),
        },
    }
    cache.set(key, result)
    result["meta"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result
