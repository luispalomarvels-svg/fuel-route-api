import random
from types import SimpleNamespace

from django.test import SimpleTestCase

from routing.services.fuel_optimizer import NoFeasiblePlanError, plan_fuel_stops


def stations(*pairs):
    """stations((mile, price), ...) -> candidates sorted by mile."""
    return [SimpleNamespace(mile=m, station=SimpleNamespace(price=p)) for m, p in sorted(pairs)]


def brute_force_cost(candidates, total, range_miles, mpg, unit):
    """Exact DP over fuel levels (in `unit`-mile steps) to cross-check the greedy plan."""
    cap = range_miles // unit
    inf = float("inf")
    best = {cap: 0.0}  # fuel level on arrival at the current point -> min cost
    pos = 0
    points = [(c.mile, c.station.price) for c in candidates] + [(total, None)]
    for i, (mile, price) in enumerate(points):
        leg = (mile - pos) // unit
        best = {f - leg: c for f, c in best.items() if f >= leg}
        if not best:
            return inf
        pos = mile
        if price is None:
            return min(best.values())
        # Buying is allowed here: arrive with f, leave with any g in [f, cap].
        after = {}
        for f, c in best.items():
            for g in range(f, cap + 1):
                cost = c + (g - f) * unit / mpg * price
                if cost < after.get(g, inf):
                    after[g] = cost
        best = after


class PlanFuelStopsTests(SimpleTestCase):
    def test_short_trip_needs_no_fuel(self):
        plan = plan_fuel_stops(stations((100, 3.0)), total_miles=450, range_miles=500, mpg=10)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0)

    def test_skips_expensive_station_when_cheaper_one_is_reachable(self):
        cands = stations((100, 4.0), (300, 3.0))
        plan = plan_fuel_stops(cands, total_miles=700, range_miles=500, mpg=10)
        self.assertEqual([s.route_station.mile for s in plan.stops], [300])
        self.assertAlmostEqual(plan.total_gallons, 20)  # 200 miles still needed after arriving with 200 left
        self.assertAlmostEqual(plan.total_cost, 60)

    def test_fills_up_at_cheap_station_before_expensive_stretch(self):
        cands = stations((100, 2.0), (450, 5.0), (800, 3.0))
        plan = plan_fuel_stops(cands, total_miles=1200, range_miles=500, mpg=10)
        self.assertEqual([round(s.gallons) for s in plan.stops], [10, 20, 40])
        self.assertAlmostEqual(plan.total_cost, 240)

    def test_buys_only_what_is_needed_to_finish(self):
        plan = plan_fuel_stops(stations((400, 3.0)), total_miles=600, range_miles=500, mpg=10)
        self.assertAlmostEqual(plan.total_gallons, 10)  # arrives at 400 with 100 left, needs 200

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(NoFeasiblePlanError):
            plan_fuel_stops(stations((100, 3.0), (700, 3.0)), total_miles=900, range_miles=500, mpg=10)

    def test_no_station_near_start_is_infeasible(self):
        with self.assertRaises(NoFeasiblePlanError):
            plan_fuel_stops(stations((600, 3.0)), total_miles=900, range_miles=500, mpg=10)

    def test_matches_brute_force_on_random_routes(self):
        rng = random.Random(42)
        checked = 0
        for _ in range(300):
            total = rng.randrange(60, 400, 10)
            cands = stations(*{(rng.randrange(10, total, 10), round(rng.uniform(2.5, 5), 2)) for _ in range(rng.randint(1, 12))})
            exact = brute_force_cost(cands, total, range_miles=100, mpg=10, unit=10)
            try:
                plan = plan_fuel_stops(cands, total, range_miles=100, mpg=10)
            except NoFeasiblePlanError:
                self.assertEqual(exact, float("inf"))
                continue
            self.assertAlmostEqual(plan.total_cost, exact, places=6)
            checked += 1
        self.assertGreater(checked, 50)
