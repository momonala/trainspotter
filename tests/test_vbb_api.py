from unittest.mock import Mock
from unittest.mock import patch

import pytest
import requests

from src.datamodels import Station
from src.vbb_api import VBBAPIError
from src.vbb_api import get_inbound_trains
from src.vbb_api import get_nearby_stations


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


ALEXANDERPLATZ_FIXTURE = {
    "type": "stop",
    "id": "900100003",
    "name": "S+U Alexanderplatz (Berlin)",
    "location": {"type": "location", "id": "900100003", "latitude": 52.521512, "longitude": 13.411267},
    "products": {
        "suburban": True,
        "subway": True,
        "tram": True,
        "bus": True,
        "ferry": False,
        "express": False,
        "regional": True,
    },
    "stationDHID": "de:11000:900100003",
}

REMOTE_FIXTURE = {
    "type": "stop",
    "id": "900260005",
    "name": "S Spandau (Berlin)",
    "location": {"type": "location", "id": "900260005", "latitude": 52.534794, "longitude": 13.197477},
    "products": {
        "suburban": True,
        "subway": False,
        "tram": False,
        "bus": True,
        "ferry": False,
        "express": True,
        "regional": True,
    },
    "stationDHID": "de:11000:900260005",
}


@patch("src.vbb_api._MAX_STRAIGHTLINE_DISTANCE_M", 1e9)
@patch("src.vbb_api._ALL_STATIONS", [ALEXANDERPLATZ_FIXTURE, REMOTE_FIXTURE])
def test_get_nearby_stations_returns_sorted_by_distance():
    coords_near_alex = (52.5219, 13.4132)
    stations = get_nearby_stations(coords_near_alex)

    assert len(stations) == 2
    assert stations[0].distance < stations[1].distance


@patch("src.vbb_api._MAX_STRAIGHTLINE_DISTANCE_M", 1e9)
@patch("src.vbb_api._ALL_STATIONS", [ALEXANDERPLATZ_FIXTURE, REMOTE_FIXTURE])
def test_get_nearby_stations_uses_config_when_no_coordinates():
    stations = get_nearby_stations()
    assert len(stations) == 2
    assert all(isinstance(s, Station) for s in stations)


@patch("src.vbb_api._ALL_STATIONS", [ALEXANDERPLATZ_FIXTURE, REMOTE_FIXTURE])
def test_get_nearby_stations_excludes_stops_beyond_straightline_radius():
    """Spandau is far from Alexanderplatz; default max_nearby_straightline_m should drop it."""
    stations = get_nearby_stations((52.5219, 13.4132))
    assert len(stations) == 1
    assert stations[0].id == ALEXANDERPLATZ_FIXTURE["id"]


@patch("src.vbb_api._ALL_STATIONS", [ALEXANDERPLATZ_FIXTURE])
def test_get_nearby_stations_computes_haversine_distance():
    stations = get_nearby_stations((52.5219, 13.4132))
    assert stations[0].distance > 0


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

    with pytest.raises(VBBAPIError) as exc_info:
        get_inbound_trains(station)

    assert "Network error" in str(exc_info.value)
