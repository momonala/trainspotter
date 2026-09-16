"""Direction heuristics: bearing math and the per-line arrow special cases, in one place."""

import math

from .datamodels import Departure


def get_initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing in degrees (0–360) from point 1 to point 2."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d_lon = lon2 - lon1
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def bearing_to_cardinal(bearing: float) -> str:
    directions = ["↑", "→", "↓", "←"]
    return directions[round(bearing / 90) % len(directions)]


def get_direction(line: str, direction: str) -> str:
    """Direction arrow for a line, with Ringbahn and station-specific overrides."""
    if line == "S41":
        return "↻"
    if line == "S42":
        return "↺"
    if direction in ["→", "↓"] and line in ["S8", "S85"]:
        return "↻"
    if direction == "←" and line == "S1":
        return "↓"
    return direction


def compute_direction(dep: Departure) -> str | None:
    """Return direction symbol (↑ ↓ ↻ ↺ ← →) from departure bearing and line, or None if unknown."""
    if not dep.stop or not dep.stop.location or not dep.destination or not dep.destination.location:
        return None
    start = dep.stop.location
    end = dep.destination.location
    bearing = get_initial_bearing(start.latitude, start.longitude, end.latitude, end.longitude)
    return get_direction(dep.line.name, bearing_to_cardinal(bearing))
