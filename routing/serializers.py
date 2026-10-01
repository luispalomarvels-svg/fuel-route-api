from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text="'City, ST', 'lat,lon', or an address in the USA")
    finish = serializers.CharField(max_length=200, help_text="'City, ST', 'lat,lon', or an address in the USA")
    geometry = serializers.ChoiceField(
        choices=["polyline", "geojson", "none"],
        default="polyline",
        help_text="How to return the route line: encoded polyline (compact), GeoJSON, or omit it.",
    )
