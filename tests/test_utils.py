from unittest.mock import Mock

import pytest

from src.datamodels import Departure
from src.datamodels import Line
from src.directions import bearing_to_cardinal
from src.directions import get_direction
from src.directions import get_initial_bearing
from src.utils import cleanse_provenance
from src.utils import cleanse_transport_type
from src.utils import get_thresholds


def test_get_thresholds():
    red, yellow = get_thresholds(15)
    assert red == 13
    assert yellow == 17


@pytest.mark.parametrize(
    "product,expected",
    [
        ("suburban", "S-Bahn"),
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


def test_get_initial_bearing_normalizes_westward_bearings_into_0_360():
    bearing = get_initial_bearing(52.5219, 13.4132, 52.5488, 13.3883)
    assert 300 < bearing < 360


@pytest.mark.parametrize(
    "bearing,expected",
    [
        (0, "↑"),
        (90, "→"),
        (180, "↓"),
        (270, "←"),
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
    ],
)
def test_get_direction(line: str, cardinal: str, expected: str):
    assert get_direction(line, cardinal) == expected
