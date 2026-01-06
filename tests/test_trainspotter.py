from datetime import datetime
from datetime import timezone
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from src.datamodels import Location
from src.datamodels import Products
from src.datamodels import Station
from src.trainspotter import get_platform_number
from src.trainspotter import get_time_color


@pytest.mark.parametrize(
    "input_val,expected",
    [
        ("1", 1),
        ("12", 12),
        ("?", float("inf")),
        ("A", float("inf")),
        ("1a", float("inf")),
    ],
)
def test_get_platform_number(input_val: str, expected: float):
    assert get_platform_number(input_val) == expected


@pytest.mark.parametrize(
    "minutes,walk_time,expected_code",
    [
        (5, 10, "\033[91m"),
        (10, 10, "\033[93m"),
        (15, 10, "\033[92m"),
        (0, 10, "\033[91m"),
        (10, None, "\033[0m"),
    ],
)
def test_get_time_color(minutes: int, walk_time: int | None, expected_code: str):
    assert get_time_color(minutes, walk_time) == expected_code


@patch("src.trainspotter.get_nearby_stations")
@patch("builtins.print")
def test_main_no_stations(mock_print, mock_get_stations):
    mock_get_stations.return_value = []

    from src.trainspotter import main

    main()

    assert any("No S-Bahn stations" in str(call) for call in mock_print.call_args_list)


@patch("src.trainspotter.get_nearby_stations")
@patch("src.trainspotter.get_inbound_trains")
@patch("src.trainspotter.get_walk_time")
@patch("src.trainspotter.get_direction")
@patch("builtins.print")
def test_main_with_stations_and_departures(
    mock_print, mock_direction, mock_walk_time, mock_get_trains, mock_get_stations
):
    mock_station = Station(
        type="stop",
        id="900000100001",
        name="Test Station",
        location=Location(type="location", id="900100001", latitude=52.5, longitude=13.4),
        products=Products(
            suburban=True, subway=False, tram=False, bus=False, ferry=False, express=False, regional=False
        ),
        stationDHID="de:11000:900100001",
        distance=100,
    )
    mock_get_stations.return_value = [mock_station]

    mock_departure = Mock()
    mock_departure.line = Mock(name="S41", product="suburban")
    mock_departure.when = datetime.now(timezone.utc)
    mock_departure.platform = "1"
    mock_departure.provenance = "Ringbahn"
    mock_get_trains.return_value = [mock_departure]
    mock_walk_time.return_value = 10
    mock_direction.return_value = "↻"

    from src.trainspotter import main

    main()

    assert any("Test Station" in str(call) for call in mock_print.call_args_list)
