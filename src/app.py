import logging
from datetime import datetime
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask
from flask import Response
from flask import jsonify
from flask import make_response
from flask import render_template
from flask import request

from .config import FLASK_PORT
from .datamodels import Departure
from .datamodels import Station
from .departures_fallback import get_fallback_departures
from .departures_fallback import get_snapshot_age_hhmmss
from .departures_fallback import store_departures_snapshot
from .image_generator import filter_and_group
from .image_generator import render_image
from .utils import config
from .utils import get_thresholds
from .utils import get_walk_time
from .utils import process_station_departures
from .vbb_api import VBBAPIError
from .vbb_api import get_inbound_trains
from .vbb_api import get_inbound_trains_cached
from .vbb_api import get_nearby_stations

basedir = Path(__file__).parent.parent
app = Flask(__name__, template_folder=str(basedir / "templates"), static_folder=str(basedir / "static"))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Global state
browser_coordinates = None
cached_stations = None
COORDINATE_ACCURACY_DECIMALS = 3


def _station_board_row(station: Station, user_coords: tuple[float, float] | None) -> dict:
    """One station's departures and timing metadata for the dashboard JSON."""
    walk_time = get_walk_time(station, user_coords)
    departures = get_inbound_trains(station)
    processed = process_station_departures(station, departures, user_coords)
    station_departures = [{k: v for k, v in row.items() if k != "departure"} for row in processed]
    red_threshold, yellow_threshold = get_thresholds(walk_time) if walk_time is not None else (None, None)
    return {
        "name": station.name,
        "distance": station.distance,
        "walkTime": walk_time,
        "departures": station_departures,
        "timeConfig": {"buffer": red_threshold, "yellowThreshold": yellow_threshold},
    }


@app.route("/")
def index():
    """Render the main page."""
    # Add cache-busting version for static assets to avoid stale iOS caches
    asset_version = int(datetime.now(timezone.utc).timestamp())
    return render_template("index.html", asset_version=asset_version)


@app.route("/api/location", methods=["POST"])
def api_location():
    """Receive and log location data from browser."""
    global browser_coordinates
    location_data = request.get_json()
    latitude = location_data.get("latitude")
    longitude = location_data.get("longitude")
    browser_coordinates = (
        round(latitude, COORDINATE_ACCURACY_DECIMALS),
        round(longitude, COORDINATE_ACCURACY_DECIMALS),
    )
    logger.info(f"📍 Received location: {browser_coordinates}")
    return jsonify({"status": "success"})


@app.route("/api/stations")
def api_stations():
    """Return station and train data as JSON."""
    logger.info("----------------------------------------------------------")
    global browser_coordinates, cached_stations

    # Check if refresh requested
    refresh = request.args.get("refresh", "false").lower() == "true"

    # Get or refresh stations
    if cached_stations is None or refresh:
        cached_stations = get_nearby_stations(browser_coordinates)
        logger.debug(f"🔄 {'Refreshed' if refresh else 'Fetched'} {len(cached_stations)} stations")
    else:
        logger.debug(f"📦 Using {len(cached_stations)} cached stations")

    logger.info(f"🚉 Processing stations for {len(cached_stations)} stations...")
    station_data = [_station_board_row(s, browser_coordinates) for s in cached_stations]
    return jsonify({"stations": station_data, "config": config})


def _fetch_esp32_departures(station_id: str, now: datetime, cache_key: str) -> tuple[list[Departure], bool] | None:
    """Return departures and source flag, or None when no valid fallback exists."""
    try:
        fresh_departures = get_inbound_trains_cached(station_id, cache_key) or []
        store_departures_snapshot(station_id, fresh_departures, now)
        logger.info(f"🫧 esp32/image: using fresh departures station_id={station_id} count={len(fresh_departures)}")
        return fresh_departures, False
    except VBBAPIError:
        fallback_departures = get_fallback_departures(station_id, now)
        if fallback_departures is None:
            logger.warning(f"❌ esp32/image: no fallback departures for station {station_id}")
            return None

        age = get_snapshot_age_hhmmss(station_id, now) or "no fallback available"
        logger.info(f"⚠️ esp32/image: using stale departures fallback {age=} {len(fallback_departures)=}")
        return fallback_departures, True


def _render_esp32_image(
    departures: list[Departure],
    now: datetime,
    station_id: str,
    cache_key: str,
    display_config: dict,
) -> Response:
    """Render the e-ink PNG response from departures."""
    if not departures:
        logger.warning(f"❌ esp32/image: no departures for station {station_id}")
        station_name = display_config.get("station_name", "")
        display_time = now.astimezone(ZoneInfo("Europe/Berlin"))
        return Response(render_image([], station_name, display_time), mimetype="image/png")

    quadrants_data = filter_and_group(
        departures,
        now,
        quadrants_config=display_config["quadrants"],
        min_minutes=config["min_departure_time_min"],
    )
    station_name = display_config["station_name"]
    display_time = now.astimezone(ZoneInfo("Europe/Berlin"))
    img = render_image(quadrants_data, station_name, display_time)
    logger.debug(f"📸 esp32/image: {station_id=} {cache_key=} size={len(img) / 1024:.1f}KB")
    return Response(img, mimetype="image/png")


@app.route("/api/esp32/image")
def api_esp32_image():
    """Generate PNG image for e-ink display."""
    display_config = config["esp32-display"]
    station_id = request.args.get("station_id", display_config["station_id"])
    now = datetime.now(timezone.utc)
    cache_key = now.strftime("%Y%m%d%H%M%S")[:-1]
    try:
        fetch_result = _fetch_esp32_departures(station_id, now, cache_key)
        if fetch_result is None:
            # empty 502 response for downstream outages.
            response = make_response("", 502)
            response.headers["Content-Length"] = "0"
            return response
        departures, _used_fallback = fetch_result
        return _render_esp32_image(departures, now, station_id, cache_key, display_config)
    except Exception as error:
        logger.exception(f"❌ esp32/image: failed to build response: {error}")
        error_response = jsonify({"error": "Image generation failed", "detail": str(error)})
        return make_response(error_response, 500)


def main():
    logger.info(f"🚀 Starting server at http://localhost:{FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=True)


if __name__ == "__main__":
    main()
