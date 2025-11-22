import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from utils import (
    bearing_to_cardinal,
    cleanse_provenance,
    cleanse_transport_type,
    config,
    get_direction,
    get_initial_bearing,
    get_thresholds,
    get_walk_time,
)
from vbb_api import get_inbound_trains, get_nearby_stations

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variable to store browser coordinates
browser_coordinates = None


@app.route("/")
def index():
    """Render the main page."""
    return render_template("index.html")


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
    logger.info(f"----------------------------------------------------------")
    global browser_coordinates
    stations = get_nearby_stations(browser_coordinates)
    station_data = []
    for station in stations:
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5007, debug=True)
