from datetime import datetime

from src.datamodels import Location
from src.datamodels import Products
from src.datamodels import parse_departures
from src.datamodels import parse_stations


def test_location_creation():
    loc = Location(type="location", id="123", latitude=52.5, longitude=13.4)
    assert loc.latitude == 52.5
    assert loc.longitude == 13.4


def test_products_creation():
    products = Products(suburban=True, subway=False, tram=False, bus=False, ferry=False, express=False, regional=False)
    assert products.suburban is True
    assert products.subway is False


def test_parse_stations_removes_berlin_suffix():
    stations_data = [
        {
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
            "distance": 100,
        }
    ]
    stations = parse_stations(stations_data)
    assert len(stations) == 1
    assert stations[0].name == "S+U Alexanderplatz "
    assert stations[0].distance == 100


def test_parse_stations_empty():
    assert parse_stations([]) == []


def test_parse_departures():
    minimal_stop = {
        "type": "stop",
        "id": "900000100001",
        "name": "S+U Alexanderplatz",
        "location": {"type": "location", "id": "900100001", "latitude": 52.521551, "longitude": 13.411511},
        "products": {
            "suburban": True,
            "subway": False,
            "tram": False,
            "bus": False,
            "ferry": False,
            "express": False,
            "regional": False,
        },
        "stationDHID": "de:11000:900100001",
    }
    departures_data = {
        "departures": [
            {
                "tripId": "1|123|0|80|1012025",
                "stop": minimal_stop,
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
    departures = parse_departures(departures_data)
    assert len(departures) == 1
    assert departures[0].line.name == "S41"
    assert departures[0].platform == "1"
    assert isinstance(departures[0].when, datetime)


def test_parse_departures_empty():
    assert parse_departures({"departures": []}) == []
