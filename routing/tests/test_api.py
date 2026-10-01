from decimal import Decimal
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from routing.models import FuelStation
from routing.services.station_index import get_station_index

# A fake 900 mile route: Chicago -> Denver along a straight line.
CHICAGO, DENVER = (41.8375, -87.6866), (39.7621, -104.8759)
LINE = [list(p) for p in np.linspace(CHICAGO, DENVER, 200)]


def fake_directions(start, finish):
    return {"polyline": "fake", "coordinates": LINE, "distance_miles": 900.0, "duration_seconds": 13.5 * 3600}


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RouteApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Three stations on the line: expensive, cheap, mid-priced.
        for opis_id, t, price in [(1, 0.2, "4.50"), (2, 0.45, "3.10"), (3, 0.7, "3.60")]:
            lat, lon = np.array(CHICAGO) + t * (np.array(DENVER) - np.array(CHICAGO))
            FuelStation.objects.create(
                opis_id=opis_id, name=f"Stop {opis_id}", address="I-80", city="X", state="NE",
                price=Decimal(price), latitude=float(lat), longitude=float(lon),
            )

    def setUp(self):
        cache.clear()
        get_station_index.cache_clear()
        patcher = mock.patch("routing.services.ors.directions", side_effect=fake_directions)
        self.directions = patcher.start()
        self.addCleanup(patcher.stop)

    def test_route_with_city_names_makes_a_single_api_call(self):
        with mock.patch("routing.services.ors.geocode") as geocode:
            resp = self.client.get(reverse("route"), {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(resp.status_code, 200, resp.content)
        geocode.assert_not_called()
        self.assertEqual(self.directions.call_count, 1)

        body = resp.json()
        self.assertEqual(body["meta"]["external_api_calls"], 1)
        # Station 2 (mile 405) is cheapest and a full tank there reaches the finish,
        # so it's the only stop: 900 - 405 - 95 miles left in tank = 400 miles = 40 gal.
        self.assertEqual([s["opis_id"] for s in body["fuel_stops"]], [2])
        self.assertAlmostEqual(body["fuel_summary"]["total_fuel_cost"], 40 * 3.10, places=1)
        self.assertIn("/api/route/map/", body["map_url"])

    def test_second_request_is_served_from_cache(self):
        params = {"start": "Chicago, IL", "finish": "Denver, CO"}
        self.client.get(reverse("route"), params)
        resp = self.client.post(reverse("route"), params, content_type="application/json")
        self.assertTrue(resp.json()["meta"]["cached"])
        self.assertEqual(self.directions.call_count, 1)

    def test_geojson_geometry_option(self):
        resp = self.client.get(reverse("route"), {"start": "41.8,-87.6", "finish": "Denver, CO", "geometry": "geojson"})
        route = resp.json()["route"]
        self.assertEqual(route["geojson"]["type"], "LineString")
        self.assertNotIn("polyline", route)

    def test_location_outside_usa_is_rejected(self):
        resp = self.client.get(reverse("route"), {"start": "48.8566,2.3522", "finish": "Denver, CO"})  # Paris
        self.assertEqual(resp.status_code, 400)
        self.directions.assert_not_called()

    def test_canadian_city_is_rejected_without_calling_geocoder(self):
        with mock.patch("routing.services.ors.geocode") as geocode:
            resp = self.client.get(reverse("route"), {"start": "Toronto, ON", "finish": "Denver, CO"})
        self.assertEqual(resp.status_code, 400)
        geocode.assert_not_called()

    def test_missing_parameters(self):
        resp = self.client.get(reverse("route"), {"start": "Chicago, IL"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("finish", resp.json())

    def test_map_page_renders(self):
        resp = self.client.get(reverse("route-map"), {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "leaflet")
        self.assertContains(resp, "Stop 2")
