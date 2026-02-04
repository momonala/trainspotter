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
    browser_coordinates = (round(latitude, 4), round(longitude, 4))
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
        logger.info(f"🔄 {'Refreshed' if refresh else 'Fetched'} {len(cached_stations)} stations")
    else:
        logger.info(f"📦 Using {len(cached_stations)} cached stations")

    station_data = []
    logger.info(f"🚉 Processing stations for {len(cached_stations)} stations...")
    for station in cached_stations:
        walk_time = get_walk_time(station, browser_coordinates)
        departures = get_inbound_trains(station)
        processed_departures = process_station_departures(station, departures, browser_coordinates)

        # Remove departure object reference before JSON serialization
        station_departures = [{k: v for k, v in d.items() if k != "departure"} for d in processed_departures]

        red_threshold, yellow_threshold = get_thresholds(walk_time) if walk_time is not None else (None, None)
        station_data.append(
            {
                "name": station.name,
                "distance": station.distance,
                "walkTime": walk_time,
                "departures": station_departures,
                "timeConfig": {"buffer": red_threshold, "yellowThreshold": yellow_threshold},
            }
        )
    return jsonify({"stations": station_data, "config": config})


@app.route("/api/esp32/image")
def api_esp32_image():
    """Generate PNG image for e-ink display."""
    eink = config["eink-display"]
    station_id = request.args.get("station_id", eink["station_id"])
    now = datetime.now(timezone.utc)
    cache_key = now.strftime("%Y%m%d%H%M%S")[:-1]
    try:
        departures = get_inbound_trains_cached(station_id, cache_key) or []
    except VBBAPIError as e:
        logger.warning("esp32/image: downstream VBB API failed: %s", e)
        # error_response = jsonify({"error": "Downstream API unavailable", "detail": str(e)})
        # response = make_response(error_response, 502)
        response = make_response("", 502)
        response.headers["Content-Length"] = "0"
        return response
    except Exception as e:
        logger.exception("esp32/image: failed to fetch departures: %s", e)
        error_response = jsonify({"error": "Departures fetch failed", "detail": str(e)})
        response = make_response(error_response, 500)
        return response

    try:
        if not departures:
            logger.warning("esp32/image: no departures for station %s", station_id)
            station_name = eink.get("station_name", "")
            display_time = now.astimezone(ZoneInfo("Europe/Berlin"))
            return Response(render_image([], station_name, display_time), mimetype="image/png")

        quadrants_data = filter_and_group(
            departures,
            now,
            quadrants_config=eink["quadrants"],
            min_minutes=config["min_departure_time_min"],
        )
        station_name = eink["station_name"]
        display_time = now.astimezone(ZoneInfo("Europe/Berlin"))
        img = render_image(quadrants_data, station_name, display_time)
        logger.info("esp32/image: station_id=%s cache_key=%s size=%.1fKB", station_id, cache_key, len(img) / 1024)
        return Response(img, mimetype="image/png")
    except Exception as e:
        logger.exception("esp32/image: failed to build or render image: %s", e)
        error_response = jsonify({"error": "Image generation failed", "detail": str(e)})
        response = make_response(error_response, 500)
        return response


def main():
    app.run(host="0.0.0.0", port=5007, debug=True)


if __name__ == "__main__":
    main()
