from datetime import datetime

from src.datamodels import parse_departures
from src.datamodels import parse_stations

MINIMAL_STOP = {
    "type": "stop",
    "id": "900000100001",
    "name": "S+U Alexanderplatz (Berlin)",
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


def _departure_dict(**overrides) -> dict:
    departure = {
        "tripId": "1|123|0|80|1012025",
        "stop": MINIMAL_STOP,
        "when": "2025-01-09T12:30:00+01:00",
        "plannedWhen": "2025-01-09T12:30:00+01:00",
        "platform": "1",
        "provenance": "via Friedrichstraße",
        "line": {"type": "line", "id": "s41", "name": "S41", "product": "suburban", "mode": "train"},
        "destination": None,
    }
    departure.update(overrides)
    return departure


def test_parse_stations_removes_berlin_suffix():
    stations = parse_stations([{**MINIMAL_STOP, "distance": 100}])
    assert len(stations) == 1
    assert stations[0].name == "S+U Alexanderplatz "
    assert stations[0].distance == 100


def test_parse_stations_ignores_unknown_fields():
    stations = parse_stations([{**MINIMAL_STOP, "someNewVbbField": "x"}])
    assert stations[0].id == "900000100001"
    assert stations[0].products.suburban is True


def test_parse_stations_empty():
    assert parse_stations([]) == []


def test_parse_departures():
    departures = parse_departures({"departures": [_departure_dict()]})
    assert len(departures) == 1
    assert departures[0].line.name == "S41"
    assert departures[0].stop.name == "S+U Alexanderplatz "
    assert isinstance(departures[0].when, datetime)


def test_parse_departures_skips_cancelled_trips():
    departures = parse_departures({"departures": [_departure_dict(when=None), _departure_dict()]})
    assert len(departures) == 1


def test_parse_departures_empty():
    assert parse_departures({"departures": []}) == []
