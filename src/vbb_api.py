import json
import logging
from datetime import datetime
from functools import lru_cache

import requests
from joblib import Memory
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .datamodels import Departure
from .datamodels import Station
from .datamodels import parse_departures
from .datamodels import parse_stations

logger = logging.getLogger(__name__)


class VBBAPIError(Exception):
    """Raised when the VBB/downstream API fails (timeout, connection, 5xx)."""


disk_cache = Memory(".cache", verbose=0)

# Load configuration
with open("config.json", "r") as f:
    config = json.load(f)

# Configure requests session with retries and timeouts
session = requests.Session()
retry_strategy = Retry(
    total=3,  # number of retries
    backoff_factor=0.5,  # wait 0.5, 1, 2 seconds between retries
    status_forcelist=[500, 502, 503, 504],  # retry on these status codes
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
session.mount("http://", adapter)
session.mount("https://", adapter)

# Request timeout in seconds
TIMEOUT = 5


@disk_cache.cache
def get_nearby_stations(coordinates: tuple[float, float] | None = None) -> list[Station]:
    """Get nearby stations that have S-Bahn service.
    Args:
        coordinates: Optional tuple of (latitude, longitude). If None, uses config coordinates.
    """
    logger.info("🔦 Fetching nearby stations...")
    if coordinates:
        logger.info(f"Using provided coordinates: {coordinates}")
    else:
        coordinates = (config["location"]["latitude"], config["location"]["longitude"])
        logger.info(f"Using config coordinates: {config['location']['latitude']}, {config['location']['longitude']}")
    lat, long = coordinates
    try:
        location_resp = session.get(
            "https://v6.vbb.transport.rest/locations/nearby",
            params={"latitude": lat, "longitude": long, "results": 20},
            timeout=TIMEOUT,
        )
        location_resp.raise_for_status()
        stations = location_resp.json()
        parsed_stations = parse_stations(stations)
        parsed_stations.sort(key=lambda station: not station.products.suburban)
        logger.info(f"👀 Found {len(parsed_stations)} nearby stations")
        return parsed_stations
    except requests.RequestException as e:
        logger.error(f"Failed to fetch nearby stations: {e}")
        raise VBBAPIError(f"VBB API error: {e}") from e


@lru_cache(maxsize=32)
def get_inbound_trains_cached(station_id: str, timestamp: str) -> list[Departure]:
    """Cached version of get_inbound_trains."""
    try:
        logger.debug(f"🔦 Fetching departures for station {station_id}...")
        departures_resp = session.get(
            f"https://v6.vbb.transport.rest/stops/{station_id}/departures",
            params={
                "duration": config["update_interval_min"],
                "linesOfStops": False,
                "remarks": False,
                "language": "en",
            },
            timeout=TIMEOUT,
        )
        departures_resp.raise_for_status()
        departures_data = departures_resp.json()
        parsed_departures = parse_departures(departures_data)
        logger.debug(f"👀 Found {len(parsed_departures)} departures for station {station_id}")
        return parsed_departures

    except requests.RequestException as e:
        logger.error(f"Failed to get departures for station {station_id}: {e}")
        raise VBBAPIError(f"VBB API error: {e}") from e


def get_inbound_trains(station: Station) -> list[Departure]:
    """Get inbound trains for a given station."""
    # Create a timestamp that changes every 30 seconds for caching
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M") + ("30" if now.second >= 30 else "00")
    return get_inbound_trains_cached(station.id, timestamp)
