"""Tests for quadrant filtering and grouping logic."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import Mock

from src.quadrants import filter_and_group

NOW = datetime(2026, 3, 24, 8, 0, 0, tzinfo=UTC)


def _make_departure(
    line_name: str,
    minutes_until: float,
    provenance: str = "Endstation",
    trip_id: str = "trip-1",
    headsign: str = "Somewhere",
) -> Mock:
    """Minimal departure heading due north, so compute_direction yields "↑"."""
    dep = Mock()
    dep.tripId = trip_id
    dep.line.name = line_name
    dep.stop = Mock(location=Mock(latitude=52.5, longitude=13.4))
    dep.destination = Mock(location=Mock(latitude=52.6, longitude=13.4))
    dep.when = NOW + timedelta(minutes=minutes_until)
    dep.provenance = provenance
    dep.direction = headsign
    return dep


QUADRANTS_CONFIG = [
    {"key": "s1_up", "label": "S1/26", "lines": ["S1", "S26"], "direction": "↑"},
    {"key": "s1_down", "label": "S1/26", "lines": ["S1", "S26"], "direction": "↓"},
    {"key": "s8_up", "label": "S8/85", "lines": ["S8"], "direction": "↑"},
    {"key": "s8_ring", "label": "S8/85", "lines": ["S8"], "direction": "↻"},
]


def test_filter_and_group_returns_one_empty_quadrant_per_config_in_order():
    result = filter_and_group([], NOW, QUADRANTS_CONFIG)
    assert [(q.key, q.label, q.arrow, q.departures) for q in result] == [
        (q["key"], q["label"], q["direction"], []) for q in QUADRANTS_CONFIG
    ]


def test_filter_and_group_keeps_departures_at_min_minutes_and_drops_sooner():
    too_soon = _make_departure("S1", minutes_until=4.9, trip_id="trip-too-soon")
    on_time = _make_departure("S1", minutes_until=5, trip_id="trip-on-time")
    result = filter_and_group([too_soon, on_time], NOW, QUADRANTS_CONFIG, min_minutes=5)
    assert len(result[0].departures) == 1
    slot = result[0].departures[0]
    assert slot.tripId == "trip-on-time"
    assert slot.line == "S1"
    assert slot.provenance == "Endstation"
    assert slot.when == on_time.when


def test_filter_and_group_keeps_every_departure_sorted_by_soonest():
    deps = [_make_departure("S1", minutes_until=m, trip_id=f"trip-{m}") for m in (20, 8, 14, 30)]
    result = filter_and_group(deps, NOW, QUADRANTS_CONFIG, min_minutes=5)
    assert [s.tripId for s in result[0].departures] == ["trip-8", "trip-14", "trip-20", "trip-30"]


def test_filter_and_group_routes_by_direction(monkeypatch):
    dep_up = _make_departure("S1", minutes_until=10, trip_id="trip-up")
    dep_down = _make_departure("S1", minutes_until=12, trip_id="trip-down")
    monkeypatch.setattr("src.quadrants.compute_direction", lambda dep: "↑" if dep is dep_up else "↓")

    result = filter_and_group([dep_up, dep_down], NOW, QUADRANTS_CONFIG, min_minutes=5)

    assert [s.tripId for s in result[0].departures] == ["trip-up"]
    assert [s.tripId for s in result[1].departures] == ["trip-down"]


def test_filter_and_group_drops_terminating_arrivals():
    dep = _make_departure("S1", 10, headsign="Fahrt endet hier")
    result = filter_and_group([dep], NOW, QUADRANTS_CONFIG)
    assert result[0].departures == []


def test_filter_and_group_keeps_earliest_of_duplicate_trip():
    later = _make_departure("S1", 11, trip_id="loop-trip")
    earlier = _make_departure("S1", 10, trip_id="loop-trip")
    result = filter_and_group([later, earlier], NOW, QUADRANTS_CONFIG)
    assert [s.when for s in result[0].departures] == [earlier.when]


def test_filter_and_group_keeps_later_pass_when_first_is_too_soon():
    too_soon = _make_departure("S1", 1, trip_id="loop-trip")
    later = _make_departure("S1", 20, trip_id="loop-trip")
    result = filter_and_group([too_soon, later], NOW, QUADRANTS_CONFIG, min_minutes=5)
    assert [s.when for s in result[0].departures] == [later.when]
