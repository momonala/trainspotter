import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask
from flask import jsonify
from flask import make_response
from flask import redirect
from flask import render_template
from flask import request
from spyglass import MetricsCollector
from spyglass import configure_logging

from .config import FLASK_PORT
from .config import PROJECT_NAME
from .config import SPYGLASS_HOST
from .config import config
from .datamodels import Station
from .quadrants import filter_and_group
from .utils import get_configured_walk_time
from .utils import get_thresholds
from .utils import get_walk_time
from .utils import process_station_departures
from .vbb_api import VBBAPIError
from .vbb_api import get_departures
from .vbb_api import get_inbound_trains
from .vbb_api import get_nearby_stations

logger = logging.getLogger(__name__)

configure_logging(host=SPYGLASS_HOST, project=PROJECT_NAME)
metrics = MetricsCollector(host=SPYGLASS_HOST, project=PROJECT_NAME)

basedir = Path(__file__).parent.parent
app = Flask(__name__, template_folder=str(basedir / "templates"), static_folder=str(basedir / "static"))
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ~111m precision — coarse enough to keep the joblib Google Maps cache hitting
# for the same effective location across geolocation jitter.
COORDINATE_ACCURACY_DECIMALS = 3

# Cache-busting version for static assets: stable per process so assets cache
# between page loads, but stale iOS caches bust on every deploy/restart.
ASSET_VERSION = int(datetime.now(timezone.utc).timestamp())


def _station_board_row(station: Station, user_coords: tuple[float, float] | None) -> dict:
    """One station's departures and timing metadata for the dashboard JSON."""
    walk_time = get_walk_time(station, user_coords)
    departures = get_inbound_trains(station)
    station_departures = process_station_departures(station, departures, user_coords)
    red_threshold, yellow_threshold = get_thresholds(walk_time) if walk_time is not None else (None, None)
    return {
        "name": station.name,
        "distance": station.distance,
        "walkTime": walk_time,
        "departures": station_departures,
        "timeConfig": {"buffer": red_threshold, "yellowThreshold": yellow_threshold},
    }


def _build_station_board_rows(
    stations: list[Station],
    user_coords: tuple[float, float] | None,
) -> list[dict]:
    """Fetch departures for all stations in parallel and build dashboard rows."""
    if not stations:
        return []
    with ThreadPoolExecutor(max_workers=len(stations)) as executor:
        return list(executor.map(lambda s: _station_board_row(s, user_coords), stations))


@app.route("/")
def index():
    """Render the main page."""
    return render_template("index.html", asset_version=ASSET_VERSION)


@app.route("/display")
def display():
    """Render the iPad display page."""
    return render_template("display.html", asset_version=ASSET_VERSION)


@app.route("/observability")
def observability():
    """Redirect to the Spyglass-hosted observability dashboard."""
    return redirect(f"http://{SPYGLASS_HOST}/dashboard/trainspotter")


def _user_coords_from_request() -> tuple[float, float]:
    """Coordinates from lat/lon query params, falling back to the config location."""
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is None or lon is None:
        return config["location"]["latitude"], config["location"]["longitude"]
    return round(lat, COORDINATE_ACCURACY_DECIMALS), round(lon, COORDINATE_ACCURACY_DECIMALS)


@app.route("/api/stations")
@metrics.timed("stations")
def api_stations():
    """Return nearby stations with live departures as JSON. Stateless: coords come per request."""
    user_coords = _user_coords_from_request()
    max_stations = config.get("max_dashboard_stations")

    nearby = get_nearby_stations(user_coords)
    stations = nearby[:max_stations] if max_stations else nearby
    logger.info("Resolved %d stations for coords %s", len(stations), user_coords)

    station_data = _build_station_board_rows(stations, user_coords)
    return jsonify({"stations": station_data})


@app.route("/api/display/data")
@metrics.timed("display_data")
def api_display_data():
    """Return quadrant departure data for the fixed display station as JSON."""
    display_config = config["display"]
    station_id = display_config["station_id"]
    now = datetime.now(timezone.utc)

    try:
        departures = get_departures(station_id)
    except VBBAPIError as error:
        logger.warning("VBB API error [%s]: %s", error.kind, error)
        diagnostics = {"station_id": station_id, **error.to_diagnostics()}
        metrics.increment("response.502", tags={"route": "display_data"})
        return make_response(
            jsonify(
                {
                    "error": error.summary,
                    "detail": str(error),
                    "diagnostics": diagnostics,
                }
            ),
            502,
        )

    try:
        # No cap: return every matching departure. The display shows 3 per quadrant
        # and reveals the rest via horizontal scroll (see .departures-row in display.css).
        quadrants_data = filter_and_group(
            departures,
            now,
            quadrants_config=display_config["quadrants"],
            min_minutes=config["min_departure_time_min"],
        )

        walk_time = get_configured_walk_time(display_config["station_name"])

        timestamp = now.astimezone(ZoneInfo("Europe/Berlin"))
        return jsonify(
            {
                "station_name": display_config["station_name"],
                "walk_time": walk_time,
                "timestamp": timestamp.isoformat(),
                "min_departure_min": config["min_departure_time_min"],
                "quadrants": [
                    {
                        "key": q.key,
                        "label": q.label,
                        "arrow": q.arrow,
                        "lines": next(c["lines"] for c in display_config["quadrants"] if c["key"] == q.key),
                        "departures": [
                            {
                                "tripId": d.tripId,
                                "when": d.when.isoformat(),
                                "line": d.line,
                                "provenance": d.provenance,
                            }
                            for d in q.departures
                        ],
                    }
                    for q in quadrants_data
                ],
            }
        )
    except Exception as error:
        logger.exception("Failed to fetch display data: %s", error)
        return make_response(jsonify({"error": "Failed to fetch display data", "detail": str(error)}), 500)


def main():
    logger.info("Starting server at http://localhost:%s", FLASK_PORT)
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)


if __name__ == "__main__":
    main()
