"""Small, dependency-light geometry helpers (all distances in miles)."""

import numpy as np

EARTH_RADIUS_MILES = 3958.8


def to_unit_xyz(lat, lon):
    """Convert lat/lon (degrees, scalars or arrays) to points on the unit sphere.

    Euclidean distance between these points is monotonic with great-circle
    distance, which lets us use a plain KD-tree for "nearest" queries.
    """
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def miles_to_chord(miles):
    """Great-circle distance in miles -> straight-line distance on the unit sphere."""
    return 2 * np.sin(np.asarray(miles) / (2 * EARTH_RADIUS_MILES))


def chord_to_miles(chord):
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.clip(np.asarray(chord) / 2, 0, 1))


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def cumulative_miles(coords):
    """Distance along a polyline at every vertex. `coords` is an (N, 2) array of [lat, lon]."""
    coords = np.asarray(coords, dtype=float)
    seg = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    return np.concatenate(([0.0], np.cumsum(seg)))


def densify(coords, max_step_miles=1.0):
    """Insert points so no segment is longer than `max_step_miles`.

    Highways are often drawn with long straight segments; densifying keeps the
    "nearest route vertex" a good approximation of "nearest point on the route".
    """
    coords = np.asarray(coords, dtype=float)
    seg = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    steps = np.maximum(1, np.ceil(seg / max_step_miles).astype(int))  # sub-points per segment
    seg_idx = np.repeat(np.arange(len(seg)), steps)
    # Fraction along each segment for every new point: 1/n, 2/n, ..., n/n.
    offsets = np.arange(len(seg_idx)) - np.repeat(np.cumsum(steps) - steps, steps) + 1
    t = (offsets / steps[seg_idx])[:, None]
    points = coords[seg_idx] + t * (coords[seg_idx + 1] - coords[seg_idx])
    return np.vstack((coords[:1], points))


def decode_polyline(encoded, precision=5):
    """Decode a Google-style encoded polyline into a list of [lat, lon]."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10**precision
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lat / factor, lon / factor])
    return coords
