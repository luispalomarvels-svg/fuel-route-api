# Fuel Route API

A Django API that plans a driving route between two US locations and picks the
**cheapest fuel stops** along the way for a vehicle with a 500-mile range at 10 MPG.
It returns the route, each fuel stop, the total fuel cost, and an interactive map.

- **One external API call per request.** The only call is for directions
  (OpenRouteService). City names are resolved offline, and repeat requests are cached.
- **Fast.** Once the route comes back, matching stations and optimizing stops
  takes about 10 ms, even on a coast-to-coast trip.
- **Provably optimal fuel plan.** It uses the greedy "gas station problem"
  algorithm, and a test checks it against an exact brute-force solver.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then add your free ORS_API_KEY
python manage.py migrate
python manage.py load_stations    # ~3 s, geocodes stations offline
python manage.py runserver
```

To get an API key, sign up for free at <https://openrouteservice.org/dev/#/signup>
(2,000 routes per day).

## API

### `GET /api/route/?start=...&finish=...`
### `POST /api/route/` with `{"start": "...", "finish": "...", "geometry": "polyline"}`

`start` and `finish` accept any of these:

| Format | Example | External calls |
|---|---|---|
| `City, ST` | `Chicago, IL` | 0 (resolved offline with the Census Gazetteer) |
| `lat,lon` | `41.8781,-87.6298` | 0 |
| Any address | `1600 Pennsylvania Ave NW, Washington, DC` | 1 geocoding call |

`geometry` is optional. It sets how the route line is returned: `polyline`
(the default, a compact encoded string), `geojson` (a LineString), or `none`.

Example response (trimmed, values illustrative):

```json
{
  "start":  {"label": "New York, NY", "lat": 40.66, "lon": -73.94, "resolved_by": "gazetteer"},
  "finish": {"label": "Los Angeles, CA", "lat": 34.02, "lon": -118.41, "resolved_by": "gazetteer"},
  "route": {"distance_miles": 2789.4, "duration_hours": 41.2, "polyline": "..."},
  "fuel_summary": {
    "total_fuel_cost": 812.45,
    "total_gallons_purchased": 228.9,
    "fuel_used_gallons": 278.9,
    "number_of_stops": 6,
    "average_price_paid": 3.549,
    "vehicle_range_miles": 500, "vehicle_mpg": 10,
    "assumptions": "Vehicle starts with a full tank; buys only the fuel it needs; arrives empty."
  },
  "fuel_stops": [
    {"stop": 1, "name": "PILOT #123", "city": "Carlisle", "state": "PA", "mile_marker": 181.3,
     "price_per_gallon": 3.199, "gallons": 18.1, "cost": 57.9, "lat": 40.2, "lon": -77.19,
     "off_route_miles": 1.2, "opis_id": 123, "address": "I-81, EXIT 52"}
  ],
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "meta": {"cached": false, "external_api_calls": 1, "stations_considered": 212, "elapsed_ms": 640.3}
}
```

Errors return `{"error": "..."}` with one of these status codes: `400` for an
invalid or non-US location, `422` when no plan is possible (a gap between stations
longer than 500 miles), or `502` when the routing provider fails.

### `GET /api/route/map/?start=...&finish=...`

An interactive Leaflet/OpenStreetMap page showing the route, numbered fuel stops
and a cost summary. It's served from the same cache, so opening it makes no extra
API calls.

A ready-made Postman collection is in [`postman_collection.json`](postman_collection.json).

## How it works

```
request ─► resolve start/finish ─► 1× ORS directions ─► stations near route ─► greedy optimizer ─► JSON + map
            (offline gazetteer)       (cached)              (KD-tree, ~10 ms)       (O(n·k))
```

1. **Loading the stations, done once** (`routing/management/commands/load_stations.py`).
   The price list has no coordinates, and geocoding about 8,000 rows at request
   time would be slow and would use up the API quota. Instead, each stop is placed
   at its city's centroid using the free **US Census Bureau Gazetteer**. This matches
   about 96% of towns with no network access. Canadian rows are dropped. For station
   IDs listed more than once, the cheapest price is kept. The remaining small
   hamlets can be geocoded once with `--geocode-missing`, and the results are saved
   to `data/geocode_cache.json`.

2. **Finding stations along the route** (`routing/services/station_index.py`).
   Stations are loaded into memory once per process. The route is densified to
   points about 1 mile apart, a KD-tree is built over those points (as 3-D
   unit-sphere vectors), and every station is matched to its nearest route point in
   a single vectorized query. That gives each station a *mile marker*. Stations
   farther than `ROUTE_CORRIDOR_MILES` (10 miles by default) from the route are
   ignored.

3. **Choosing where to buy fuel** (`routing/services/fuel_optimizer.py`). At each
   station:
   - If a cheaper station is within range, buy just enough fuel to reach the
     *nearest* one.
   - Otherwise, this is the cheapest fuel for the next 500 miles, so fill up (or buy
     just enough to finish) and head to the cheapest station in range.

   This greedy strategy is optimal. `test_matches_brute_force_on_random_routes`
   checks it against an exact dynamic-programming solver on 300 random routes.

## Assumptions

- The vehicle starts with a **full tank**, buys only what it needs, and arrives
  empty. `total_fuel_cost` is the money spent at stops. `fuel_used_gallons` is the
  total fuel the trip burns.
- Stations are placed at their **city centroid** because the price list has no
  coordinates. As a result, the small detour to reach each station is ignored.
- Routing uses the ORS `driving-car` profile. Set `ORS_PROFILE=driving-hgv` for
  truck-specific routing.

## Tests

```bash
python manage.py test routing
```

The tests cover the optimizer (including the brute-force optimality check), place
name matching (e.g. "Oklahoma City city", "Nashville-Davidson (balance)",
"St. Louis"), and the API end to end with the routing provider mocked out.

## Project layout

```
config/                     Django settings & root URLs
routing/
  models.py                 FuelStation
  views.py                  /api/route/ and /api/route/map/
  services/
    planner.py              orchestration, input resolution, caching
    ors.py                  OpenRouteService client (directions + geocoding)
    places.py               offline US place lookup (Census Gazetteer)
    station_index.py        in-memory spatial index of stations
    fuel_optimizer.py       cheapest refuelling plan
    geo.py                  distance / polyline helpers
  management/commands/load_stations.py
  templates/routing/map.html
data/                       fuel prices CSV + Census Gazetteer
```
