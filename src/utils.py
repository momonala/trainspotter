"""Shared utility functions for trainspotter."""

import logging
import math
from datetime import datetime
from datetime import timezone

import googlemaps
from joblib import Memory

from .config import basedir
from .config import config
from .datamodels import Departure
from .datamodels import Station
from .values import GMAPS_API_KEY

logger = logging.getLogger(__name__)

disk_cache = Memory(str(basedir / ".cache"), verbose=0)


@disk_cache.cache
def _get_walk_time_gmaps(origin: tuple[float, float], destination: tuple[float, float], station_name: str) -> int:
    gmaps = googlemaps.Client(key=GMAPS_API_KEY)
    result = gmaps.directions(origin=origin, destination=destination, mode="walking")
    duration_sec = result[0]["legs"][0]["duration"]["value"]
    duration_min = duration_sec / 60
    logger.info(
        "Google Maps walking time for %s: %.1f minutes between %s and %s",
        station_name,
        duration_min,
        origin,
        destination,
    )
    return math.ceil(duration_min)


def get_configured_walk_time(station_name: str) -> int | None:
    """Return configured walk time when a config station key matches the name."""
    station_name_lower = station_name.lower()
    station_key = next((k for k in config["stations"] if k in station_name_lower), None)
    if station_key is None:
        return None
    return config["stations"][station_key]["walk_time"]


def get_walk_time(station: Station, current_coordinates: tuple[float, float] | None = None) -> int | None:
    """Walk time to a station: from config when the station is configured, else via Google Maps.

    Raises:
        ValueError: If the station is not configured and no current coordinates were given.
    """
    walk_time = get_configured_walk_time(station.name)
    if walk_time is not None:
        logger.debug("Station %s is configured with walk time %d minutes", station.name, walk_time)
        return walk_time
    if current_coordinates is None:
        raise ValueError(f"current_coordinates required for unconfigured station {station.name!r}")
    destination_coordinates = (round(station.location.latitude, 4), round(station.location.longitude, 4))
    return _get_walk_time_gmaps(current_coordinates, destination_coordinates, station.name)


def get_thresholds(walk_time: int) -> tuple[int, int]:
    """Calculate red and yellow thresholds based on walk time.
    Returns (red_threshold, yellow_threshold) where:
    - Under red_threshold: red
    - Between red and yellow: yellow
    - Above yellow: green
    """
    red_threshold = walk_time - config["walk_time_buffer"]
    yellow_threshold = walk_time + config["walk_time_buffer"]
    return red_threshold, yellow_threshold


_TRANSPORT_TYPE_BY_PRODUCT = {
    "suburban": "S-Bahn",
    "subway": "U-Bahn",
    "tram": "Tram",
    "bus": "Bus",
    "regional": "DB",
    "express": "DB",
}


def cleanse_transport_type(departure: Departure) -> str:
    """Map a departure's VBB product to a display transport type."""
    return _TRANSPORT_TYPE_BY_PRODUCT.get(departure.line.product.lower(), "other")


def cleanse_provenance(provenance: str, max_length: int = 28) -> str:
    """Cleanse the provenance string."""
    if "Hauptbahnhof" in provenance:
        provenance = provenance.replace("Hauptbahnhof", "HBF")
    elif ", Bahnhof" in provenance:
        provenance = provenance.replace(", Bahnhof", "")
    elif "(Berlin)" in provenance:
        provenance = provenance.replace("(Berlin)", "")
    elif "Bhf" in provenance:
        provenance = provenance.replace("Bhf", "")
    if "S+U" in provenance:
        provenance = provenance.replace("S+U", "")
    if "(TF)" in provenance:
        provenance = provenance.replace("(TF)", "")
    if "S " in provenance:
        provenance = provenance.replace("S ", "")
    if "U " in provenance:
        provenance = provenance.replace("U ", "")
    if "[Gleis 1-8]" in provenance:
        provenance = provenance.replace("[Gleis 1-8]", "")
    return provenance[:max_length].strip()


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
    """Get the platform direction with emoji for specific stations."""
    if line == "S41":
        return "↻"
    elif line == "S42":
        return "↺"
    elif direction in ["→", "↓"] and line in ["S8", "S85"]:
        return "↻"
    elif direction == "←" and line == "S1":
        return "↓"
    else:
        return direction


def process_station_departures(
    station: Station, departures: list[Departure], browser_coordinates: tuple[float, float] | None = None
) -> list[dict]:
    """Process departures for a station, calculating directions and wait times.

    Returns list of processed departure dicts with direction_symbol, wait_time, etc.
    """

    walk_time = get_walk_time(station, browser_coordinates)
    now = datetime.now(timezone.utc)

    processed = []
    for departure in departures:
        if not departure.stop or not departure.stop.location:
            continue
        if not departure.destination or not departure.destination.location:
            continue

        start = departure.stop.location
        stop = departure.destination.location
        bearing = get_initial_bearing(start.latitude, start.longitude, stop.latitude, stop.longitude)
        cardinal = bearing_to_cardinal(bearing)
        direction_symbol = get_direction(departure.line.name, cardinal)

        minutes_until = int((departure.when - now).total_seconds() / 60)
        wait_time = minutes_until - (walk_time or 0)

        processed.append(
            {
                "transport_type": cleanse_transport_type(departure),
                "line": departure.line.name,
                "when": departure.when.isoformat(),
                "direction_symbol": direction_symbol,
                "provenance": cleanse_provenance(departure.destination.name),
                "wait_time": wait_time,
            }
        )

    processed.sort(key=lambda x: x["when"])
    return processed
