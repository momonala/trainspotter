"""Tests for quadrant filtering and grouping logic."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import Mock

import pytest

from src.quadrants import DepartureSlot
from src.quadrants import QuadrantData
from src.quadrants import filter_and_group


def _make_departure(
    line_name: str,
    minutes_until: int,
    direction: str | None = "↑",
    provenance: str = "Endstation",
    trip_id: str = "trip-1",
) -> Mock:
    """Build a minimal mock Departure with the fields filter_and_group needs."""
    location = Mock(latitude=52.5, longitude=13.4)
    stop = Mock(location=location)
    destination = Mock(location=Mock(latitude=52.6, longitude=13.4))

    dep = Mock()
    dep.tripId = trip_id
    dep.line = Mock(name=line_name)
    dep.line.name = line_name
    dep.stop = stop
    dep.destination = destination
    dep.when = datetime.now(timezone.utc) + timedelta(minutes=minutes_until)
    dep.provenance = provenance
    return dep


QUADRANTS_CONFIG = [
    {"key": "s1_up", "label": "S1/26", "lines": ["S1", "S26"], "direction": "↑"},
    {"key": "s1_down", "label": "S1/26", "lines": ["S1", "S26"], "direction": "↓"},
    {"key": "s8_up", "label": "S8/85", "lines": ["S8"], "direction": "↑"},
    {"key": "s8_ring", "label": "S8/85", "lines": ["S8"], "direction": "↻"},
]


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


def test_filter_and_group_returns_one_entry_per_config(now):
    result = filter_and_group([], now, QUADRANTS_CONFIG)
    assert len(result) == 4


def test_filter_and_group_empty_departures_yields_empty_quadrants(now):
    result = filter_and_group([], now, QUADRANTS_CONFIG)
    assert all(q.departures == [] for q in result)


def test_filter_and_group_assigns_label_and_arrow(now):
    result = filter_and_group([], now, QUADRANTS_CONFIG)
    assert result[0].key == "s1_up"
    assert result[0].label == "S1/26"
    assert result[0].arrow == "↑"
    assert result[2].key == "s8_up"
    assert result[2].label == "S8/85"
    assert result[2].arrow == "↑"


def test_filter_and_group_excludes_departures_within_min_minutes(now):
    dep = _make_departure("S1", minutes_until=3)
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.quadrants.compute_direction", lambda _: "↑")
        result = filter_and_group([dep], now, QUADRANTS_CONFIG, min_minutes=5)
    assert all(q.departures == [] for q in result)


def test_filter_and_group_includes_departures_above_min_minutes(now):
    dep = _make_departure("S1", minutes_until=10, trip_id="trip-s1-10")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.quadrants.compute_direction", lambda _: "↑")
        result = filter_and_group([dep], now, QUADRANTS_CONFIG, min_minutes=5)
    s1_up = result[0]
    assert len(s1_up.departures) == 1
    slot = s1_up.departures[0]
    assert slot.tripId == "trip-s1-10"
    assert slot.line == "S1"
    assert slot.provenance == "Endstation"
    assert 9 <= slot.minutes <= 11


def test_filter_and_group_rejects_missing_trip_id(now):
    dep = _make_departure("S1", minutes_until=10, trip_id="")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.quadrants.compute_direction", lambda _: "↑")
        with pytest.raises(ValueError, match="missing tripId"):
            filter_and_group([dep], now, QUADRANTS_CONFIG, min_minutes=5)


def test_filter_and_group_caps_at_max_per_quadrant(now):
    deps = [_make_departure("S1", minutes_until=10 + i, trip_id=f"trip-{i}") for i in range(5)]
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.quadrants.compute_direction", lambda _: "↑")
        result = filter_and_group(deps, now, QUADRANTS_CONFIG, min_minutes=5, max_per_quadrant=2)
    assert len(result[0].departures) == 2


def test_filter_and_group_keeps_all_departures_by_default(now):
    deps = [_make_departure("S1", minutes_until=10 + i, trip_id=f"trip-{i}") for i in range(8)]
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.quadrants.compute_direction", lambda _: "↑")
        result = filter_and_group(deps, now, QUADRANTS_CONFIG, min_minutes=5)
    assert len(result[0].departures) == 8


def test_filter_and_group_sorts_by_soonest(now):
    deps = [
        _make_departure("S1", minutes_until=20, trip_id="trip-late"),
        _make_departure("S1", minutes_until=8, trip_id="trip-soon"),
        _make_departure("S1", minutes_until=14, trip_id="trip-mid"),
    ]
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.quadrants.compute_direction", lambda _: "↑")
        result = filter_and_group(deps, now, QUADRANTS_CONFIG, min_minutes=5, max_per_quadrant=2)
    minutes_list = [s.minutes for s in result[0].departures]
    assert minutes_list == sorted(minutes_list)
    assert result[0].departures[0].tripId == "trip-soon"


def test_filter_and_group_routes_by_direction(now):
    dep_up = _make_departure("S1", minutes_until=10, trip_id="trip-up")
    dep_down = _make_departure("S1", minutes_until=12, trip_id="trip-down")

    def fake_direction(dep):
        return "↑" if dep.when < dep_down.when else "↓"

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.quadrants.compute_direction", fake_direction)
        result = filter_and_group([dep_up, dep_down], now, QUADRANTS_CONFIG, min_minutes=5)

    assert len(result[0].departures) == 1  # s1_up
    assert len(result[1].departures) == 1  # s1_down
    assert result[0].departures[0].tripId == "trip-up"
    assert result[1].departures[0].tripId == "trip-down"


def test_quadrant_data_dataclass():
    slot = DepartureSlot(tripId="trip-1", minutes=7, line="S1", provenance="Oranienburg")
    q = QuadrantData(key="s1_up", label="S1/26", arrow="↑", departures=[slot])
    assert q.key == "s1_up"
    assert q.label == "S1/26"
    assert q.arrow == "↑"
    assert q.departures == [slot]
