from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import Mock
from unittest.mock import patch

import pytest

import src.app as app_module
from src.app import app
from src.datamodels import Color
from src.datamodels import Departure
from src.datamodels import Line
from src.datamodels import Location
from src.datamodels import Operator
from src.datamodels import Products
from src.datamodels import Station
from src.vbb_api import VBBAPIError

TEST_STATION_ID = "900110011"
BASE_TIME_UTC = datetime(2026, 3, 24, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app_module.browser_coordinates = None
    app_module.cached_stations = None
    with app.test_client() as client:
        yield client


@pytest.fixture
def base_now_utc() -> datetime:
    return BASE_TIME_UTC


@pytest.fixture
def station_location() -> Location:
    return Location(type="location", id="loc-1", latitude=52.5, longitude=13.4)


@pytest.fixture
def station_products() -> Products:
    return Products(
        suburban=True,
        subway=False,
        tram=False,
        bus=False,
        ferry=False,
        express=False,
        regional=False,
    )


@pytest.fixture
def test_station(station_location: Location, station_products: Products) -> Station:
    return Station(
        type="station",
        id="900000100001",
        name="Test Station",
        location=station_location,
        products=station_products,
        stationDHID="de:11000:100001",
        distance=0,
    )


@pytest.fixture
def suburban_line() -> Line:
    operator = Operator(type="operator", id="op-1", name="DB")
    color = Color(fg="#ffffff", bg="#000000")
    return Line(
        type="line",
        id="line-1",
        fahrtNr="1",
        name="S41",
        public=True,
        adminCode="admin",
        productName="S-Bahn",
        mode="train",
        product="suburban",
        operator=operator,
        color=color,
    )


@pytest.fixture
def departure_factory(test_station: Station, suburban_line: Line):
    def _build(base_now: datetime, minutes_until: int) -> Departure:
        when = base_now + timedelta(minutes=minutes_until)
        return Departure(
            tripId="trip-1",
            stop=test_station,
            when=when,
            plannedWhen=when,
            delay=None,
            platform="1",
            plannedPlatform="1",
            prognosisType=None,
            direction="Ringbahn",
            provenance="Gesundbrunnen",
            line=suburban_line,
            remarks=[],
            origin=test_station,
            destination=test_station,
            currentTripPosition=None,
        )

    return _build


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
    mock_departure.when = datetime.now(timezone.utc) + timedelta(minutes=10)
    mock_departure.stop = Mock(location=mock_location)
    mock_departure.destination = mock_destination
    return mock_departure


@pytest.fixture
def stations_api_station() -> Mock:
    location = Location(type="location", id="loc-1", latitude=52.5, longitude=13.4)
    station = Mock()
    station.name = "Test Station"
    station.distance = 100
    station.location = Mock(latitude=location.latitude, longitude=location.longitude)
    return station


# =============================================================================
# Index / main dashboard
# =============================================================================


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
        assert "(52.522, 13.413)" in str(mock_logger.info.call_args)


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

    client.post("/api/location", json={"latitude": 52.5219, "longitude": 13.4132})
    response = client.get("/api/stations")

    assert response.status_code == 200
    data = response.get_json()
    assert "stations" in data
    assert "config" in data


# =============================================================================
# /display page
# =============================================================================


def test_display_route_renders_html(client):
    response = client.get("/display")
    assert response.status_code == 200
    assert b"<!DOCTYPE html>" in response.data or b"<html" in response.data


# =============================================================================
# /api/display/data
# =============================================================================


@patch("src.app.filter_and_group", return_value=[])
@patch("src.app.get_departures", return_value=[])
def test_api_display_data_returns_expected_shape(mock_departures, mock_filter, client):
    response = client.get("/api/display/data")
    assert response.status_code == 200
    data = response.get_json()
    assert "station_name" in data
    assert "walk_time" in data
    assert "timestamp" in data
    assert "min_departure_min" in data
    assert "quadrants" in data
    assert isinstance(data["quadrants"], list)


@patch("src.app.filter_and_group")
@patch("src.app.get_departures", return_value=[])
def test_api_display_data_departures_include_trip_id(mock_departures, mock_filter, client):
    from src.quadrants import DepartureSlot
    from src.quadrants import QuadrantData

    mock_filter.return_value = [
        QuadrantData(
            key="s1_26_up",
            label="S1/26",
            arrow="↑",
            departures=[
                DepartureSlot(tripId="trip-abc", minutes=10, line="S1", provenance="Oranienburg"),
            ],
        ),
        QuadrantData(key="s1_26_down", label="S1/26", arrow="↓", departures=[]),
        QuadrantData(key="s8_up", label="S8/85", arrow="↑", departures=[]),
        QuadrantData(key="s8_clockwise", label="S8/85", arrow="↻", departures=[]),
    ]

    response = client.get("/api/display/data")
    assert response.status_code == 200
    dep = response.get_json()["quadrants"][0]["departures"][0]
    assert dep == {
        "tripId": "trip-abc",
        "minutes": 10,
        "line": "S1",
        "provenance": "Oranienburg",
    }


@patch("src.app.get_departures", return_value=[])
def test_api_display_data_quadrant_keys_match_config(mock_departures, client):
    from src.utils import config

    expected_keys = [q["key"] for q in config["display"]["quadrants"]]
    response = client.get("/api/display/data")
    assert response.status_code == 200
    actual_keys = [q["key"] for q in response.get_json()["quadrants"]]
    assert actual_keys == expected_keys


@patch("src.app.get_departures", return_value=[])
def test_api_display_data_walk_time_from_config(mock_departures, client):
    response = client.get("/api/display/data")
    assert response.status_code == 200
    assert response.get_json()["walk_time"] == 7


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
