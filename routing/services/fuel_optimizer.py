"""Cheapest refuelling plan along a fixed route.

This is the classic "gas station problem" on a line, solved with the greedy
strategy that is provably optimal when stations are points along the route:

  At each station:
    * If a cheaper station is reachable on a full tank, buy just enough fuel
      to get to the *nearest* such station, and go there.
    * Otherwise this is the cheapest fuel for the next `range` miles, so fill
      up (or buy just enough to finish, if the destination is in range) and
      drive to the cheapest station within range.

Assumptions (documented in the README):
  * The vehicle leaves the start with a full tank.
  * Fuel is only bought where it is needed; it arrives with an empty tank.
  * Stations are treated as lying on the route (small detours are ignored).
"""

from dataclasses import dataclass

EPSILON = 1e-6


class NoFeasiblePlanError(Exception):
    """A gap between stations is longer than the vehicle's range."""


@dataclass(frozen=True)
class FuelStop:
    route_station: object  # RouteStation (or anything with .mile and .station.price)
    gallons: float
    cost: float


@dataclass(frozen=True)
class FuelPlan:
    stops: list
    total_gallons: float
    total_cost: float


def plan_fuel_stops(candidates, total_miles, range_miles, mpg, start_fuel_miles=None):
    """Return the cheapest FuelPlan.

    `candidates` must be sorted by `.mile`; each needs `.mile` and `.station.price`.
    Fuel is tracked in "miles of range" and converted to gallons when bought.
    """
    fuel = range_miles if start_fuel_miles is None else start_fuel_miles
    pos, current = 0.0, None  # current = index into candidates, None at the origin
    stops = []

    def buy(miles_of_fuel):
        nonlocal fuel
        if miles_of_fuel <= EPSILON:
            return
        cs = candidates[current]
        gallons = miles_of_fuel / mpg
        stops.append(FuelStop(cs, gallons, gallons * cs.station.price))
        fuel += miles_of_fuel

    while pos + fuel < total_miles - EPSILON:
        # At the origin we can't buy fuel, so we can only reach what's in the tank.
        reach = pos + (range_miles if current is not None else fuel)
        first = 0 if current is None else current + 1
        reachable = []
        for k in range(first, len(candidates)):
            if candidates[k].mile > reach + EPSILON:
                break
            reachable.append(k)

        if current is None:
            if not reachable:
                raise NoFeasiblePlanError(f"No fuel station within {fuel:.0f} miles of the start.")
            nxt = reachable[0]
        else:
            price = candidates[current].station.price
            cheaper = next((k for k in reachable if candidates[k].station.price < price), None)
            if cheaper is not None:
                nxt = cheaper
                buy(candidates[nxt].mile - pos - fuel)
            elif total_miles - pos <= range_miles + EPSILON:
                buy(total_miles - pos - fuel)
                break
            elif not reachable:
                raise NoFeasiblePlanError(
                    f"No fuel station within {range_miles:.0f} miles after mile {pos:.0f}."
                )
            else:
                buy(range_miles - fuel)
                # Cheapest in range; on ties prefer the farthest to cover more ground.
                nxt = min(reachable, key=lambda k: (candidates[k].station.price, -candidates[k].mile))

        fuel -= candidates[nxt].mile - pos
        pos, current = candidates[nxt].mile, nxt

    return FuelPlan(
        stops=stops,
        total_gallons=sum(s.gallons for s in stops),
        total_cost=sum(s.cost for s in stops),
    )
