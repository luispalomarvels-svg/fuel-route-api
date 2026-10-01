from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.fuel_optimizer import NoFeasiblePlanError
from .services.ors import RoutingError
from .services.planner import LocationError, plan_route

ERROR_STATUS = {
    LocationError: status.HTTP_400_BAD_REQUEST,
    NoFeasiblePlanError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    RoutingError: status.HTTP_502_BAD_GATEWAY,
}


def _plan_or_error(start, finish):
    """Returns (result, None) on success or (None, (status_code, message)) on a known failure."""
    try:
        return plan_route(start, finish), None
    except tuple(ERROR_STATUS) as exc:
        return None, (ERROR_STATUS[type(exc)], str(exc))


class RouteView(APIView):
    """
    Plan a trip between two US locations with the cheapest fuel stops.

    GET  /api/route/?start=Chicago, IL&finish=Denver, CO
    POST /api/route/  {"start": "Chicago, IL", "finish": "Denver, CO"}
    """

    def get(self, request):
        return self._handle(request, request.query_params)

    def post(self, request):
        return self._handle(request, request.data)

    def _handle(self, request, data):
        serializer = RouteRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        params = serializer.validated_data

        result, error = _plan_or_error(params["start"], params["finish"])
        if error:
            return Response({"error": error[1]}, status=error[0])

        route = dict(result["route"])
        coordinates = route.pop("coordinates")
        if params["geometry"] == "geojson":
            route.pop("polyline")
            route["geojson"] = {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in coordinates]}
        elif params["geometry"] == "none":
            route.pop("polyline")

        query = urlencode({"start": params["start"], "finish": params["finish"]})
        return Response(
            {
                **result,
                "route": route,
                "map_url": request.build_absolute_uri(f"{reverse('route-map')}?{query}"),
            }
        )


def route_map(request):
    """Interactive Leaflet map of the route and fuel stops (served from the same cache)."""
    start, finish = request.GET.get("start", ""), request.GET.get("finish", "")
    if not start or not finish:
        return render(request, "routing/map.html", {"error": "Provide ?start=...&finish=..."}, status=400)
    result, error = _plan_or_error(start, finish)
    if error:
        return render(request, "routing/map.html", {"error": error[1]}, status=error[0])
    return render(request, "routing/map.html", {"result": result})
