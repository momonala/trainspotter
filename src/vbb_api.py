import json
import logging
import math
from datetime import datetime
from functools import lru_cache
from operator import itemgetter
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .datamodels import Departure
from .datamodels import Station
from .datamodels import parse_departures
from .datamodels import parse_stations

logger = logging.getLogger(__name__)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


class VBBAPIError(Exception):
    """Raised when the VBB/downstream API fails (timeout, connection, 5xx)."""


# Load configuration
with open("config.json", "r") as f:
    config = json.load(f)

_MAX_STRAIGHTLINE_DISTANCE_M = float(config.get("max_nearby_straightline_m", 1500))


def _load_station_snapshot(path: Path) -> list[dict]:
    """Load the static stop list produced by scripts/fetch_stations.py."""
    if not path.exists():
        raise FileNotFoundError(
            f"Station data not found at {path}. Run `python scripts/fetch_stations.py` to generate it."
        )
    return json.loads(path.read_text(encoding="utf-8"))


_STATIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "vbb_stations.json"
_ALL_STATIONS: list[dict] = _load_station_snapshot(_STATIONS_PATH)
logger.info(f"📂 Loaded {len(_ALL_STATIONS)} stations from {_STATIONS_PATH.name}")

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

MAX_NEARBY_STATIONS = 20
_EARTH_RADIUS_M = 6_371_000


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _rank_stops_by_distance(
    stops: list[dict],
    latitude: float,
    longitude: float,
    limit: int,
    max_straightline_m: float,
) -> list[tuple[float, dict]]:
    """Sort by geodesic distance; keep stops within `max_straightline_m`, then take the closest `limit`."""
    pairs: list[tuple[float, dict]] = []
    for stop in stops:
        loc = stop["location"]
        meters = _haversine_meters(latitude, longitude, loc["latitude"], loc["longitude"])
        if meters > max_straightline_m:
            continue
        pairs.append((meters, stop))
    pairs.sort(key=itemgetter(0))
    return pairs[:limit]


def _suburban_first_sort_key(station: Station) -> int:
    """S-Bahn stops sort before others; stable sort keeps distance order within each group."""
    return 0 if station.products.suburban else 1


def get_nearby_stations(coordinates: tuple[float, float] | None = None) -> list[Station]:
    """Return the closest stops from the local snapshot within straight-line radius, S-Bahn first."""
    if coordinates is not None:
        lat, lon = coordinates
        logger.debug(f"📍 Using provided coordinates: {coordinates}")
    else:
        lat = config["location"]["latitude"]
        lon = config["location"]["longitude"]
        logger.debug(f"📍 Using config coordinates: ({lat}, {lon})")
    nearest = _rank_stops_by_distance(_ALL_STATIONS, lat, lon, MAX_NEARBY_STATIONS, _MAX_STRAIGHTLINE_DISTANCE_M)
    station_dicts = [{**stop, "distance": int(round(meters))} for meters, stop in nearest]
    parsed = parse_stations(station_dicts)
    parsed.sort(key=_suburban_first_sort_key)
    logger.info(f"👀 Found {len(parsed)} nearby stations")
    return parsed


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
        logger.debug(f"Failed to get departures for station {station_id}: {e}")
        raise VBBAPIError(f"VBB API error: {e}") from e


def get_inbound_trains(station: Station) -> list[Departure]:
    """Get inbound trains for a given station."""
    # Create a timestamp that changes every 30 seconds for caching
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M") + ("30" if now.second >= 30 else "00")
    return get_inbound_trains_cached(station.id, timestamp)
