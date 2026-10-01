from django.test import SimpleTestCase

from routing.services.places import census_name_variants, get_place_index


class PlaceLookupTests(SimpleTestCase):
    def test_census_suffixes_are_removed(self):
        self.assertEqual(census_name_variants("Tomah city")[0], "Tomah")
        self.assertEqual(census_name_variants("Oklahoma City city")[0], "Oklahoma City")
        self.assertIn("Nashville", census_name_variants("Nashville-Davidson metropolitan government (balance)"))

    def test_lookup_handles_real_world_names(self):
        places = get_place_index()
        for city, state in [
            ("Oklahoma City", "OK"),
            ("Nashville", "TN"),
            ("St. Louis", "MO"),
            ("Saint Louis", "MO"),
            ("Carson City", "NV"),
            ("Pecos", "TX"),
            ("Big Cabin", "OK"),
        ]:
            with self.subTest(city=city):
                self.assertIsNotNone(places.lookup(city, state))

    def test_same_name_in_other_state_is_not_matched(self):
        self.assertIsNone(get_place_index().lookup("Tomah", "TX", fuzzy=False))
