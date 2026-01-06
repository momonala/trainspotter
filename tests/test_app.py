from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from src.app import app
from src.vbb_api import disk_cache


@pytest.fixture
def client():
    app.config["TESTING"] = True
    import src.app as app_module

    app_module.browser_coordinates = None
    app_module.cached_stations = None
    disk_cache.clear()
    with app.test_client() as client:
        yield client
    disk_cache.clear()


def test_index_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"<!DOCTYPE html>" in response.data or b"<html" in response.data


def test_api_location_post(client):
    response = client.post("/api/location", json={"latitude": 52.5219, "longitude": 13.4132})
    assert response.status_code == 200
    assert response.get_json()["status"] == "success"


def test_api_location_rounds_coordinates(client):
    with patch("src.app.logger") as mock_logger:
        client.post("/api/location", json={"latitude": 52.521951234, "longitude": 13.413245678})
        mock_logger.info.assert_called_once()
        assert "(52.522, 13.4132)" in str(mock_logger.info.call_args)


@patch("src.app.get_nearby_stations")
@patch("src.app.get_inbound_trains")
def test_api_stations_returns_json(mock_get_trains, mock_get_stations, client):
    mock_station = Mock()
    mock_station.name = "Test Station"
    mock_station.distance = 100
    mock_station.location = Mock(latitude=52.5, longitude=13.4)

    mock_location = Mock(latitude=52.5, longitude=13.4)
    mock_destination = Mock(location=mock_location)
    mock_destination.name = "Spandau"

    mock_line = Mock()
    mock_line.name = "S41"
    mock_line.product = "suburban"

    mock_departure = Mock()
    mock_departure.line = mock_line
    mock_departure.when = datetime.now(timezone.utc) + timedelta(minutes=10)
    mock_departure.stop = Mock(location=mock_location)
    mock_departure.destination = mock_destination

    mock_get_stations.return_value = [mock_station]
    mock_get_trains.return_value = [mock_departure]

    client.post("/api/location", json={"latitude": 52.5219, "longitude": 13.4132})
    response = client.get("/api/stations")

    assert response.status_code == 200
    data = response.get_json()
    assert "stations" in data
    assert "config" in data
