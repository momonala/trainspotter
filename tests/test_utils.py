from unittest.mock import Mock

import pytest

from src.datamodels import Departure
from src.datamodels import Line
from src.utils import bearing_to_cardinal
from src.utils import cleanse_provenance
from src.utils import cleanse_transport_type
from src.utils import get_direction
from src.utils import get_initial_bearing
from src.utils import get_thresholds


def test_get_thresholds():
    red, yellow = get_thresholds(15)
    assert red == 13
    assert yellow == 17


@pytest.mark.parametrize(
    "product,expected",
    [
        ("suburban", "S-Bahn"),
        ("subway", "U-Bahn"),
        ("tram", "Tram"),
        ("bus", "Bus"),
        ("regional", "DB"),
        ("express", "DB"),
        ("ferry", "other"),
    ],
)
def test_cleanse_transport_type(product: str, expected: str):
    departure = Mock(spec=Departure)
    departure.line = Mock(spec=Line)
    departure.line.product = product
    assert cleanse_transport_type(departure) == expected


@pytest.mark.parametrize(
    "input_text,expected",
    [
        ("Berlin Hauptbahnhof", "Berlin HBF"),
        ("Potsdam, Bahnhof", "Potsdam"),
        ("Alexanderplatz (Berlin)", "Alexanderplatz"),
        ("S+U Gesundbrunnen Bhf", "Gesundbrunnen"),
        ("S Bornholmer Str.", "Bornholmer Str."),
        ("U Seestraße", "Seestraße"),
    ],
)
def test_cleanse_provenance(input_text: str, expected: str):
    assert cleanse_provenance(input_text) == expected


def test_cleanse_provenance_truncates():
    result = cleanse_provenance("A" * 50, max_length=10)
    assert len(result) == 10


@pytest.mark.parametrize(
    "lat1,lon1,lat2,lon2,min_deg,max_deg",
    [
        (52.5219, 13.4132, 52.5488, 13.3883, 300, 360),
        (52.5, 13.0, 52.5, 13.5, 80, 100),
    ],
)
def test_get_initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float, min_deg: float, max_deg: float):
    bearing = get_initial_bearing(lat1, lon1, lat2, lon2)
    assert min_deg < bearing < max_deg


@pytest.mark.parametrize(
    "bearing,expected",
    [
        (0, "↑"),
        (45, "↑"),
        (90, "→"),
        (135, "↓"),
        (180, "↓"),
        (225, "↓"),
        (270, "←"),
        (315, "↑"),
        (359, "↑"),
    ],
)
def test_bearing_to_cardinal(bearing: float, expected: str):
    assert bearing_to_cardinal(bearing) == expected


@pytest.mark.parametrize(
    "line,cardinal,expected",
    [
        ("S41", "↑", "↻"),
        ("S42", "↓", "↺"),
        ("S8", "→", "↻"),
        ("S85", "↓", "↻"),
        ("S1", "←", "↓"),
        ("S1", "→", "→"),
        ("S2", "↑", "↑"),
    ],
)
def test_get_direction(line: str, cardinal: str, expected: str):
    assert get_direction(line, cardinal) == expected
