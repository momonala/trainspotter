from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from freezegun import freeze_time

import src.app as app_module
import src.departures_fallback as departures_fallback_module
from src.app import app
from src.datamodels import Color
from src.datamodels import Departure
from src.datamodels import Line
from src.datamodels import Location
from src.datamodels import Operator
from src.datamodels import Products
from src.datamodels import Station
from src.vbb_api import VBBAPIError
from src.vbb_api import disk_cache

TEST_STATION_ID = "900110011"
BASE_TIME_UTC = datetime(2026, 3, 24, 8, 0, 0, tzinfo=timezone.utc)


def _clear_departure_snapshots() -> None:
    departures_fallback_module._snapshots_by_station_id.clear()


@pytest.fixture
def client():
    app.config["TESTING"] = True

    app_module.browser_coordinates = None
    app_module.cached_stations = None
    _clear_departure_snapshots()
    disk_cache.clear()
    with app.test_client() as client:
        yield client
    _clear_departure_snapshots()
    disk_cache.clear()


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
def esp32_image_response_with_mocked_time(client):
    def _request(first_now: datetime, second_now: datetime):
        with freeze_time(first_now):
            client.get("/api/esp32/image")
        with freeze_time(second_now):
            return client.get("/api/esp32/image")

    return _request


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


@patch("src.app.render_image", return_value=b"fake-png")
@patch("src.app.filter_and_group", return_value=[])
@patch("src.app.get_inbound_trains_cached")
def test_api_esp32_image_uses_stale_fallback_on_vbb_error(
    mock_get_trains,
    _mock_filter,
    _mock_render,
    client,
    base_now_utc,
    departure_factory,
):
    fresh_departure = departure_factory(base_now_utc, minutes_until=16)
    mock_get_trains.side_effect = [[fresh_departure], VBBAPIError("downstream unavailable")]

    with freeze_time(base_now_utc):
        first = client.get(f"/api/esp32/image?station_id={TEST_STATION_ID}")
    with freeze_time(base_now_utc + timedelta(seconds=20)):
        second = client.get(f"/api/esp32/image?station_id={TEST_STATION_ID}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.mimetype == "image/png"


@patch("src.app.render_image", return_value=b"fake-png")
@patch("src.app.filter_and_group", return_value=[])
@patch("src.app.get_inbound_trains_cached")
def test_api_esp32_image_returns_200_when_unpassed_stale_data_exists(
    mock_get_trains,
    _mock_filter,
    _mock_render,
    esp32_image_response_with_mocked_time,
    base_now_utc,
    departure_factory,
):
    nearly_expired_departure = departure_factory(base_now_utc, minutes_until=6)
    mock_get_trains.side_effect = [[nearly_expired_departure], VBBAPIError("downstream unavailable")]

    response = esp32_image_response_with_mocked_time(
        first_now=base_now_utc,
        second_now=base_now_utc + timedelta(minutes=1),
    )

    assert response.status_code == 200


@patch("src.app.render_image", return_value=b"fake-png")
@patch("src.app.filter_and_group", return_value=[])
@patch("src.app.get_inbound_trains_cached")
def test_api_esp32_image_returns_502_when_all_stale_departures_are_passed(
    mock_get_trains,
    _mock_filter,
    _mock_render,
    esp32_image_response_with_mocked_time,
    base_now_utc,
    departure_factory,
):
    short_departure = departure_factory(base_now_utc, minutes_until=1)
    mock_get_trains.side_effect = [[short_departure], VBBAPIError("downstream unavailable")]

    response = esp32_image_response_with_mocked_time(
        first_now=base_now_utc,
        second_now=base_now_utc + timedelta(minutes=2),
    )

    assert response.status_code == 502
