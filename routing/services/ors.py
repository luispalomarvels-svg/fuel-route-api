"""Thin client for the OpenRouteService API (https://openrouteservice.org).

Free tier: 2,000 directions and 1,000 geocoding requests per day.
"""

import requests
from django.conf import settings

from .geo import decode_polyline

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    """The routing provider failed or could not find a route."""


def _api_key():
    if not settings.ORS_API_KEY:
        raise RoutingError("ORS_API_KEY is not configured. Add it to your .env file.")
    return settings.ORS_API_KEY


def geocode(text):
    """Resolve free text to (lat, lon, label), restricted to the USA. One API call."""
    try:
        resp = requests.get(
            f"{settings.ORS_BASE_URL}/geocode/search",
            params={"api_key": _api_key(), "text": text, "boundary.country": "US", "size": 1},
            timeout=settings.ORS_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f"Geocoding request failed: {exc}") from exc
    features = resp.json().get("features") or []
    if not features:
        return None
    lon, lat = features[0]["geometry"]["coordinates"]
    return lat, lon, features[0]["properties"].get("label", text)


def directions(start, finish):
    """Driving route between two (lat, lon) points. One API call.

    Returns a dict with the decoded geometry as [[lat, lon], ...], the raw encoded
    polyline, distance in miles and duration in seconds.
    """
    body = {
        "coordinates": [[start[1], start[0]], [finish[1], finish[0]]],
        "instructions": False,
        "units": "m",
    }
    try:
        resp = requests.post(
            f"{settings.ORS_BASE_URL}/v2/directions/{settings.ORS_PROFILE}",
            json=body,
            headers={"Authorization": _api_key()},
            timeout=settings.ORS_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RoutingError(f"Routing request failed: {exc}") from exc
    if resp.status_code != 200:
        try:
            message = resp.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = resp.text[:200]
        raise RoutingError(f"Routing provider error ({resp.status_code}): {message}")

    route = resp.json()["routes"][0]
    return {
        "polyline": route["geometry"],
        "coordinates": decode_polyline(route["geometry"]),
        "distance_miles": route["summary"]["distance"] / METERS_PER_MILE,
        "duration_seconds": route["summary"]["duration"],
    }
