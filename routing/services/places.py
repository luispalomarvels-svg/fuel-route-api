"""Offline US place lookup backed by the US Census Bureau Gazetteer.

Used for two things:
  * geocoding truck stops (city + state -> lat/lon) when loading the price list
  * resolving "City, ST" inputs to the API without calling any external service
"""

import csv
import difflib
import re
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from scipy.spatial import cKDTree

from .geo import chord_to_miles, to_unit_xyz

# Census place names carry a legal/statistical suffix: "Tomah city", "Abanda CDP",
# "Nashville-Davidson metropolitan government (balance)".
_SUFFIX_RE = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|metro government|"
    r"unified government|urban county|municipality|corporation|plantation|borough|village|"
    r"city|town|cdp)(\s*\(balance\))?$",
    re.IGNORECASE,
)
_ABBREVIATIONS = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount", "pt": "point"}


def normalize_place_name(name):
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("'", "").replace(".", "")).split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def census_name_variants(census_name):
    """Names a Census place is commonly known by, most specific first.

    "Oklahoma City city"                                    -> ["Oklahoma City"]
    "Nashville-Davidson metropolitan government (balance)"  -> ["Nashville-Davidson", "Nashville"]
    "Boise City city"                                       -> ["Boise City", "Boise"]
    """
    name = re.sub(r"\s*\(balance\)$", "", census_name.strip(), flags=re.IGNORECASE)
    name = re.sub(r"^(town|city|village) of\s+", "", name, flags=re.IGNORECASE)  # "Town of Pecos city"
    name = _SUFFIX_RE.sub("", name)
    variants = [name, census_name.strip()]  # full name covers suffix-less entries like "Carson City"
    for sep in ("-", "/"):
        if sep in name:
            variants.append(name.split(sep)[0])
    if name.lower().endswith(" city"):
        variants.append(name[:-5])
    return variants


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lon: float


class PlaceIndex:
    def __init__(self, places):
        self._by_key = {}
        self._names_by_state = {}
        aliases = []
        for place, land_area in places:
            primary, *others = census_name_variants(place.name)
            key = (place.state, normalize_place_name(primary))
            # Several entries can share a name (e.g. a city and a CDP); keep the biggest.
            if key not in self._by_key or land_area > self._by_key[key][1]:
                self._by_key[key] = (place, land_area)
            aliases.extend(((place.state, normalize_place_name(o)), place, land_area) for o in others)
        # Aliases ("Nashville" for "Nashville-Davidson") never override a real place name.
        for key, place, land_area in aliases:
            self._by_key.setdefault(key, (place, land_area))
        for state, norm in self._by_key:
            self._names_by_state.setdefault(state, []).append(norm)
        self._places = [p for p, _ in self._by_key.values()]
        self._tree = cKDTree(to_unit_xyz([p.lat for p in self._places], [p.lon for p in self._places]))

    @classmethod
    def from_gazetteer(cls, path):
        places = []
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            reader.fieldnames = [h.strip() for h in reader.fieldnames]
            for row in reader:
                places.append(
                    (
                        Place(row["NAME"].strip(), row["USPS"], float(row["INTPTLAT"]), float(row["INTPTLONG"].strip())),
                        int(row["ALAND"]),
                    )
                )
        return cls(places)

    def lookup(self, city, state, fuzzy=True):
        """Find a place by city name + 2-letter state code. Returns a Place or None."""
        state = state.strip().upper()
        norm = normalize_place_name(city)
        hit = self._by_key.get((state, norm))
        if hit:
            return hit[0]
        if fuzzy:
            close = difflib.get_close_matches(norm, self._names_by_state.get(state, []), n=1, cutoff=0.88)
            if close:
                return self._by_key[(state, close[0])][0]
        return None

    def nearest(self, lat, lon):
        """Nearest known US place and its distance in miles."""
        chord, idx = self._tree.query(to_unit_xyz(lat, lon)[0])
        return self._places[idx], float(chord_to_miles(chord))


@lru_cache(maxsize=1)
def get_place_index():
    return PlaceIndex.from_gazetteer(settings.GAZETTEER_PATH)
