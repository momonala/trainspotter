from unittest.mock import Mock
from unittest.mock import patch

import pytest
import requests

from src.datamodels import Station
from src.vbb_api import disk_cache
from src.vbb_api import get_inbound_trains
from src.vbb_api import get_nearby_stations


@pytest.fixture(autouse=True)
def clear_cache():
    disk_cache.clear()
    yield
    disk_cache.clear()


def _minimal_stop():
    return {
        "type": "stop",
        "id": "900000100001",
        "name": "S+U Alexanderplatz",
        "location": {"type": "location", "id": "900100001", "latitude": 52.521551, "longitude": 13.411511},
        "products": {
            "suburban": True,
            "subway": True,
            "tram": True,
            "bus": True,
            "ferry": False,
            "express": False,
            "regional": True,
        },
        "stationDHID": "de:11000:900100001",
    }


@patch("src.vbb_api.session.get")
def test_get_nearby_stations_success(mock_get):
    mock_response = Mock()
    mock_response.json.return_value = [{**_minimal_stop(), "distance": 100}]
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    stations = get_nearby_stations((52.5219, 13.4132))

    assert len(stations) == 1
    assert stations[0].name == "S+U Alexanderplatz"
    assert stations[0].distance == 100
    mock_get.assert_called_once()


@patch("src.vbb_api.session.get")
@patch("src.vbb_api.logger")
def test_get_nearby_stations_handles_errors(mock_logger, mock_get):
    mock_get.side_effect = requests.RequestException("Network error")

    stations = get_nearby_stations((52.5219, 13.4132))

    assert stations == []
    mock_logger.error.assert_called_once()


@patch("src.vbb_api.session.get")
def test_get_inbound_trains_success(mock_get):
    mock_response = Mock()
    mock_response.json.return_value = {
        "departures": [
            {
                "tripId": "1|123|0|80|1012025",
                "stop": _minimal_stop(),
                "when": "2025-01-09T12:30:00+01:00",
                "plannedWhen": "2025-01-09T12:30:00+01:00",
                "delay": None,
                "platform": "1",
                "plannedPlatform": "1",
                "prognosisType": None,
                "direction": "Spandau",
                "provenance": "via Friedrichstraße",
                "line": {
                    "type": "line",
                    "id": "s41",
                    "fahrtNr": "12345",
                    "name": "S41",
                    "public": True,
                    "adminCode": "80____",
                    "productName": "S",
                    "mode": "train",
                    "product": "suburban",
                },
                "remarks": [],
                "origin": None,
                "destination": None,
            }
        ]
    }
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    station = Mock(spec=Station)
    station.id = "900000100001"

    departures = get_inbound_trains(station)

    assert len(departures) == 1
    assert departures[0].line.name == "S41"


@patch("src.vbb_api.session.get")
@patch("src.vbb_api.logger")
def test_get_inbound_trains_handles_errors(mock_logger, mock_get):
    mock_get.side_effect = requests.RequestException("Network error")

    station = Mock(spec=Station)
    station.id = "900000999999"

    departures = get_inbound_trains(station)

    assert departures == []
    mock_logger.error.assert_called()
