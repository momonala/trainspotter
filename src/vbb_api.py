import json
import logging
import math
import time
from operator import itemgetter
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from spyglass import MetricsCollector

from .config import PROJECT_NAME
from .config import SPYGLASS_HOST
from .config import VBB_API_BASE
from .datamodels import Departure
from .datamodels import Station
from .datamodels import parse_departures
from .datamodels import parse_stations

metrics = MetricsCollector(host=SPYGLASS_HOST, project=PROJECT_NAME)

logger = logging.getLogger(__name__)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


class VBBAPIError(Exception):
    """Raised when the VBB/downstream API fails (timeout, connection, 5xx)."""

    _SUMMARIES = {
        "timeout": "VBB timed out",
        "connection": "VBB connection failed",
        "http_500": "VBB returned 500",
        "http_502": "VBB returned 502",
        "http_503": "VBB returned 503",
        "http_504": "VBB returned 504",
    }

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status

    @property
    def summary(self) -> str:
        """Short, user-facing description of the upstream failure."""
        if self.kind in self._SUMMARIES:
            return self._SUMMARIES[self.kind]
        if self.http_status is not None:
            return f"VBB returned {self.http_status}"
        return "VBB unreachable"

    def to_diagnostics(self) -> dict:
        """Structured fields for API diagnostics payloads."""
        diagnostics = {
            "vbb_error": str(self),
            "vbb_error_kind": self.kind,
            "vbb_error_summary": self.summary,
        }
        if self.http_status is not None:
            diagnostics["vbb_http_status"] = self.http_status
        return diagnostics


def _extract_http_status(exc: BaseException) -> int | None:
    """Return an HTTP status code from a requests/urllib3 exception chain, if any."""
    current: BaseException | None = exc
    while current is not None:
        response = getattr(current, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            if isinstance(status_code, int):
                return status_code
        current = current.__cause__
    return None


def _is_timeout(exc: BaseException) -> bool:
    """True when the exception chain indicates a read/connect timeout."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, requests.Timeout):
            return True
        if "timed out" in str(current).lower():
            return True
        current = current.__cause__
    return False


def _classify_request_exception(exc: requests.RequestException) -> tuple[str, int | None]:
    """Map a failed VBB request to a stable error kind and optional HTTP status."""
    http_status = _extract_http_status(exc)
    if http_status is not None:
        return f"http_{http_status}", http_status
    if _is_timeout(exc):
        return "timeout", None
    if isinstance(exc, requests.ConnectionError):
        return "connection", None
    return "unknown", None


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
    data = json.loads(path.read_text(encoding="utf-8"))
    logger.debug("Loaded %d stations from %s", len(data), path.name)
    return data


_STATIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "vbb_stations.json"
_ALL_STATIONS: list[dict] = _load_station_snapshot(_STATIONS_PATH)

session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount("http://", adapter)
session.mount("https://", adapter)

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
        logger.debug("Using provided coordinates: %s", coordinates)
    else:
        lat = config["location"]["latitude"]
        lon = config["location"]["longitude"]
        logger.debug("Using config coordinates: (%s, %s)", lat, lon)
    nearest = _rank_stops_by_distance(_ALL_STATIONS, lat, lon, MAX_NEARBY_STATIONS, _MAX_STRAIGHTLINE_DISTANCE_M)
    station_dicts = [{**stop, "distance": int(round(meters))} for meters, stop in nearest]
    parsed = parse_stations(station_dicts)
    parsed.sort(key=_suburban_first_sort_key)
    logger.info("Found %d nearby stations", len(parsed))
    return parsed


def get_departures(station_id: str) -> list[Departure]:
    """Fetch departures from VBB for a stop ID."""
    started = time.perf_counter()
    try:
        departures_resp = session.get(
            f"{VBB_API_BASE}/stops/{station_id}/departures",
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
        metrics.timing("vbb.fetch", (time.perf_counter() - started) * 1000, tags={"outcome": "ok"})
        metrics.increment("vbb.success")
        return parse_departures(departures_data)

    except requests.RequestException as e:
        metrics.timing("vbb.fetch", (time.perf_counter() - started) * 1000, tags={"outcome": "error"})
        kind, http_status = _classify_request_exception(e)
        metrics.increment("vbb.error", tags={"kind": kind})
        raise VBBAPIError(f"VBB API error: {e}", kind=kind, http_status=http_status) from e


def get_inbound_trains(station: Station) -> list[Departure]:
    """Get inbound trains for a given station."""
    return get_departures(station.id)
