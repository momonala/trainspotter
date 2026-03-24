from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from src.datamodels import Color
from src.datamodels import Departure
from src.datamodels import Line
from src.datamodels import Location
from src.datamodels import Operator
from src.datamodels import Products
from src.datamodels import Station
from src.departures_fallback import _snapshots_by_station_id
from src.departures_fallback import get_fallback_departures
from src.departures_fallback import get_snapshot_age_hhmmss
from src.departures_fallback import store_departures_snapshot

TEST_STATION_ID = "900110011"
BASE_TIME_UTC = datetime(2026, 3, 24, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_snapshots():
    _snapshots_by_station_id.clear()
    yield
    _snapshots_by_station_id.clear()


def _require_fallback_departures(station_id: str, current_time_utc: datetime) -> list[Departure]:
    departures = get_fallback_departures(station_id, current_time_utc)
    assert departures is not None
    return departures


def _build_departure(base_now: datetime, minutes_until: int) -> Departure:
    location = Location(type="location", id="loc-1", latitude=52.5, longitude=13.4)
    products = Products(
        suburban=True,
        subway=False,
        tram=False,
        bus=False,
        ferry=False,
        express=False,
        regional=False,
    )
    station = Station(
        type="station",
        id="900000100001",
        name="Test Station",
        location=location,
        products=products,
        stationDHID="de:11000:100001",
        distance=0,
    )
    operator = Operator(type="operator", id="op-1", name="DB")
    color = Color(fg="#ffffff", bg="#000000")
    line = Line(
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
    when = base_now + timedelta(minutes=minutes_until)
    return Departure(
        tripId=f"trip-{minutes_until}",
        stop=station,
        when=when,
        plannedWhen=when,
        delay=None,
        platform="1",
        plannedPlatform="1",
        prognosisType=None,
        direction="Ringbahn",
        provenance="Gesundbrunnen",
        line=line,
        remarks=[],
        origin=station,
        destination=station,
        currentTripPosition=None,
    )


def test_get_fallback_departures_returns_none_without_snapshot():
    result = get_fallback_departures(TEST_STATION_ID, BASE_TIME_UTC)
    assert result is None


def test_store_departures_snapshot_ignores_empty_departures():
    store_departures_snapshot(TEST_STATION_ID, [], BASE_TIME_UTC)
    assert get_fallback_departures(TEST_STATION_ID, BASE_TIME_UTC) is None


def test_get_snapshot_age_hhmmss_formats_elapsed_time():
    captured_at = BASE_TIME_UTC
    now = captured_at + timedelta(hours=1, minutes=2, seconds=3)
    store_departures_snapshot(TEST_STATION_ID, [_build_departure(captured_at, 20)], captured_at)
    assert get_snapshot_age_hhmmss(TEST_STATION_ID, now) == "01:02:03"


def test_get_fallback_departures_shifts_and_filters_passed_departures():
    captured_at = BASE_TIME_UTC
    now = captured_at + timedelta(minutes=12)
    departures = [
        _build_departure(captured_at, 30),
        _build_departure(captured_at, 20),
        _build_departure(captured_at, 10),
    ]
    store_departures_snapshot(TEST_STATION_ID, departures, captured_at)

    fallback_departures = _require_fallback_departures(TEST_STATION_ID, now)
    remaining_minutes = [int((dep.when - now).total_seconds() / 60) for dep in fallback_departures]
    assert all(minutes >= 0 for minutes in remaining_minutes)
    assert len(remaining_minutes) < len(departures)


@pytest.mark.parametrize("minutes_until", [1, 5, 6])
def test_get_fallback_departures_returns_unpassed_departures_without_headroom_gate(minutes_until: int):
    departure = _build_departure(BASE_TIME_UTC, minutes_until)
    store_departures_snapshot(TEST_STATION_ID, [departure], BASE_TIME_UTC)
    _require_fallback_departures(TEST_STATION_ID, BASE_TIME_UTC)


def test_get_fallback_departures_shifts_planned_when_alongside_when():
    captured_at = BASE_TIME_UTC
    now = captured_at + timedelta(minutes=3)
    departure = _build_departure(captured_at, 15)
    store_departures_snapshot(TEST_STATION_ID, [departure], captured_at)

    fallback_departures = _require_fallback_departures(TEST_STATION_ID, now)
    shifted_departure = fallback_departures[0]
    assert shifted_departure.when == captured_at + timedelta(minutes=12)
    assert shifted_departure.plannedWhen == captured_at + timedelta(minutes=12)
