"""In-memory spatial index of fuel stations, built once per process."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.spatial import cKDTree

from .geo import chord_to_miles, cumulative_miles, densify, miles_to_chord, to_unit_xyz


@dataclass(frozen=True)
class Station:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lon: float


@dataclass(frozen=True)
class RouteStation:
    """A station that lies on the route, `mile` miles from the start."""

    station: Station
    mile: float
    off_route_miles: float


class StationIndex:
    def __init__(self, stations):
        # Stations are geocoded to city centroids, so every stop in a city shares one
        # point. Only the cheapest per point can ever be the best choice there.
        cheapest = {}
        for s in stations:
            key = (round(s.lat, 5), round(s.lon, 5))
            if key not in cheapest or s.price < cheapest[key].price:
                cheapest[key] = s
        self.stations = list(cheapest.values())
        self._xyz = to_unit_xyz([s.lat for s in self.stations], [s.lon for s in self.stations])

    def __len__(self):
        return len(self.stations)

    def stations_along(self, route_coords, corridor_miles, route_length_miles=None):
        """Stations within `corridor_miles` of the route, ordered by distance from the start.

        Instead of querying the station tree once per route point, we build a tree
        over the (densified) route and ask it for each station's nearest route
        vertex: a single vectorised query, typically a few milliseconds.
        """
        if not self.stations:
            return []
        dense = densify(route_coords, max_step_miles=1.0)
        along = cumulative_miles(dense)
        if route_length_miles and along[-1] > 0:
            # Scale the polyline length to the router's reported road distance.
            along *= route_length_miles / along[-1]

        route_tree = cKDTree(to_unit_xyz(dense[:, 0], dense[:, 1]))
        chord, vertex = route_tree.query(self._xyz, distance_upper_bound=miles_to_chord(corridor_miles))
        on_route = np.flatnonzero(np.isfinite(chord))

        result = [
            RouteStation(self.stations[i], float(along[vertex[i]]), float(chord_to_miles(chord[i])))
            for i in on_route
        ]
        result.sort(key=lambda rs: (rs.mile, rs.station.price))
        return result


def _load_from_db():
    from routing.models import FuelStation

    rows = FuelStation.objects.values_list(
        "opis_id", "name", "address", "city", "state", "price", "latitude", "longitude"
    )
    return StationIndex(
        Station(opis_id, name, address, city, state, float(price), lat, lon)
        for opis_id, name, address, city, state, price, lat, lon in rows
    )


@lru_cache(maxsize=1)
def get_station_index():
    """Process-wide index. Call `get_station_index.cache_clear()` after reloading stations."""
    return _load_from_db()
