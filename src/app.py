import logging
from datetime import datetime
from datetime import timezone
from pathlib import Path

from flask import Flask
from flask import jsonify
from flask import render_template
from flask import request

from .utils import bearing_to_cardinal
from .utils import cleanse_provenance
from .utils import cleanse_transport_type
from .utils import config
from .utils import get_direction
from .utils import get_initial_bearing
from .utils import get_thresholds
from .utils import get_walk_time
from .vbb_api import get_inbound_trains
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

        # Create a single list of departures for this station
        station_departures = []

        for departure in departures:
            start = departure.stop.location
            stop = departure.destination.location
            bearing = get_initial_bearing(start.latitude, start.longitude, stop.latitude, stop.longitude)
            direction = bearing_to_cardinal(bearing)
            direction = get_direction(departure.line.name, direction)

            # Calculate wait time (time until departure minus walk time)
            now = datetime.now(timezone.utc)
            minutes_until = int((departure.when - now).total_seconds() / 60)
            wait_time = minutes_until - (walk_time or 0)  # If no walk time, assume 0

            station_departures.append(
                {
                    "transport_type": cleanse_transport_type(departure),
                    "line": departure.line.name,
                    "when": departure.when.isoformat(),
                    "direction_symbol": direction,
                    "provenance": cleanse_provenance(departure.destination.name),
                    "wait_time": wait_time,
                }
            )

        # Sort all departures by time
        station_departures.sort(key=lambda x: x["when"])

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


def main():
    app.run(host="0.0.0.0", port=5007, debug=True)


if __name__ == "__main__":
    main()
