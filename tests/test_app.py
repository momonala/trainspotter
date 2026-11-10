from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from src.app import app
from src.datamodels import Location
from src.vbb_api import VBBAPIError


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def stations_api_departure() -> Mock:
    mock_location = Mock(latitude=52.5, longitude=13.4)
    mock_destination = Mock(location=mock_location)
    mock_destination.name = "Spandau"
    mock_line = Mock()
    mock_line.name = "S41"
    mock_line.product = "suburban"
    mock_departure = Mock()
    mock_departure.line = mock_line
    mock_departure.when = datetime.now(UTC) + timedelta(minutes=10)
    mock_departure.stop = Mock(location=mock_location)
    mock_departure.destination = mock_destination
    return mock_departure


@pytest.fixture
def stations_api_station() -> Mock:
    station = Mock()
    station.name = "Test Station"
    station.distance = 100
    station.location = Location(latitude=52.5, longitude=13.4)
    return station


# =============================================================================
# Index / main dashboard
# =============================================================================


@pytest.mark.parametrize("path", ["/", "/display"])
def test_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b"<html" in response.data.lower()


@patch("src.utils.get_walk_time", return_value=10)
@patch("src.app.get_walk_time", return_value=10)
@patch("src.app.get_nearby_stations")
@patch("src.app.get_inbound_trains")
def test_api_stations_returns_json(
    mock_get_trains,
    mock_get_stations,
    mock_get_walk_time_app,
    mock_get_walk_time_utils,
    client,
    stations_api_station,
    stations_api_departure,
):
    mock_get_stations.return_value = [stations_api_station]
    mock_get_trains.return_value = [stations_api_departure]

    response = client.get("/api/stations?lat=52.521951234&lon=13.413245678")

    assert response.status_code == 200
    data = response.get_json()
    assert "stations" in data
    assert "gmaps_api_key" not in str(data)
    # Coords are rounded to 3 decimals before ranking / walk-time lookup
    mock_get_stations.assert_called_once_with((52.522, 13.413))


@patch("src.app.get_nearby_stations", return_value=[])
def test_api_stations_without_coords_uses_config_fallback(mock_get_stations, client):
    from src.config import config

    response = client.get("/api/stations")
    assert response.status_code == 200
    expected = (config["location"]["latitude"], config["location"]["longitude"])
    mock_get_stations.assert_called_once_with(expected)


# =============================================================================
# /api/display/data
# =============================================================================


@patch("src.app.get_departures", return_value=[])
def test_api_display_data_follows_config(mock_departures, client):
    from src.config import config

    response = client.get("/api/display/data")
    assert response.status_code == 200
    data = response.get_json()
    assert data["walk_time"] == 7
    assert data["min_departure_min"] == config["min_departure_time_min"]
    assert "timestamp" in data
    assert [q["key"] for q in data["quadrants"]] == [q["key"] for q in config["display"]["quadrants"]]


@patch("src.app.get_departures")
def test_api_display_data_departures_include_trip_id(mock_departures, client):
    from src.quadrants import DepartureSlot
    from src.quadrants import QuadrantData

    when = datetime(2026, 3, 24, 8, 10, 0, tzinfo=UTC)
    slot = DepartureSlot(tripId="trip-abc", when=when, line="S1", provenance="Oranienburg")
    mock_departures.return_value = []

    def fake_filter(departures, now, quadrants_config, min_minutes):
        return [
            QuadrantData(
                key=q["key"],
                label=q["label"],
                arrow=q["direction"],
                departures=[slot] if q["key"] == "s1_26_up" else [],
            )
            for q in quadrants_config
        ]

    with patch("src.app.filter_and_group", side_effect=fake_filter):
        response = client.get("/api/display/data")
    assert response.status_code == 200
    dep = response.get_json()["quadrants"][0]["departures"][0]
    assert dep == {
        "tripId": "trip-abc",
        "when": "2026-03-24T08:10:00+00:00",
        "line": "S1",
        "provenance": "Oranienburg",
    }


@patch("src.app.get_departures", return_value=[])
def test_api_display_data_fetches_each_configured_stop_once(mock_departures, client):
    from src.config import config

    display = config["display"]
    expected = {q.get("station_id", display["station_id"]) for q in display["quadrants"]}
    response = client.get("/api/display/data")
    assert response.status_code == 200
    assert sorted(c.args[0] for c in mock_departures.call_args_list) == sorted(expected)


@patch("src.app.get_departures")
def test_api_display_data_returns_502_on_vbb_error(mock_get_departures, client):
    mock_get_departures.side_effect = VBBAPIError("downstream unavailable")

    response = client.get("/api/display/data")

    assert response.status_code == 502
    body = response.get_json()
    assert "diagnostics" in body
    assert body["diagnostics"]["station_id"] is not None
    assert body["error"] == "VBB unreachable"
    assert body["diagnostics"]["vbb_error_kind"] == "unknown"
