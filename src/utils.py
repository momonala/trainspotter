"""Shared utility functions for trainspotter."""

import json
import logging
import math
from pathlib import Path

import googlemaps
import numpy as np
from joblib import Memory

from .datamodels import Departure
from .datamodels import Station

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache in parent directory
basedir = Path(__file__).parent.parent
disk_cache = Memory(str(basedir / ".cache"), verbose=0)

# Load config from parent directory
config_path = basedir / "config.json"
with open(config_path, "r") as f:
    config = json.load(f)


@disk_cache.cache
def _get_walk_time_gmaps(origin: tuple[float, float], destination: tuple[float, float], station_name: str) -> int:
    gmaps = googlemaps.Client(key=config["gmaps_api_key"])
    result = gmaps.directions(origin=origin, destination=destination, mode="walking")
    duration_sec = result[0]["legs"][0]["duration"]["value"]
    duration_min = duration_sec / 60
    logger.info(
        f"🚶🏽‍♂️ Google Maps walking time for {station_name}: {duration_min:.1f} minutes between {origin} and {destination}"
    )
    return np.ceil(duration_min)


def get_walk_time(station: Station, current_coordinates: tuple[float, float] | None = None) -> int | None:
    """Get configured walk time for a station."""
    station_key = next((k for k in config["stations"].keys() if k in station.name.lower()), None)
    if station_key:
        walk_time = config["stations"][station_key]["walk_time"]
        logger.debug(f"🚶🏽‍♂️ Station {station.name} is configured with walk time {walk_time} minutes")
        return walk_time
    else:
        destination_coordinates = (round(station.location.latitude, 4), round(station.location.longitude, 4))
        assert (
            current_coordinates and destination_coordinates
        ), f"{current_coordinates=} and {destination_coordinates=} are required if Station not configured"
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


def cleanse_transport_type(departure: Departure) -> str:
    """Get the transport type for an departure."""
    product = departure.line.product.lower()
    if product == "suburban":
        return "S-Bahn"
    elif product == "subway":
        return "U-Bahn"
    elif product == "tram":
        return "Tram"
    elif product == "bus":
        return "Bus"
    elif product == "regional" or product == "express":
        return "DB"  # Deutsche Bahn regional trains
    return "other"


def get_platform_group(station_name: str, platform: str, transport_type: str) -> str:
    """Get the platform group for stations with combined platforms."""
    if "bornholmer" in station_name.lower() and transport_type == "S-Bahn":
        if platform in ["1", "2"]:
            return "1 & 2"
        elif platform in ["3", "4"]:
            return "3 & 4"
    return platform


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


def get_initial_bearing(lat1, lon1, lat2, lon2):
    # Convert to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d_lon = lon2 - lon1

    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)

    initial_bearing = math.atan2(x, y)
    bearing_degrees = (math.degrees(initial_bearing) + 360) % 360
    return bearing_degrees


def bearing_to_cardinal(bearing):
    directions = ["↑", "→", "↓", "←"]
    idx = round(bearing / 90) % len(directions)
    return directions[idx]


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
