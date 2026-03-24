from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime

from .datamodels import Departure


@dataclass(frozen=True)
class CachedDeparturesSnapshot:
    """Last successful departures payload for one station."""

    captured_at_utc: datetime
    departures: list[Departure]


_snapshots_by_station_id: dict[str, CachedDeparturesSnapshot] = {}


def _format_hhmmss(total_seconds: int) -> str:
    """Format elapsed seconds as HH:MM:SS."""
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def store_departures_snapshot(station_id: str, departures: list[Departure], captured_at_utc: datetime) -> None:
    """Store a departures snapshot for fallback usage."""
    if not departures:
        return

    _snapshots_by_station_id[station_id] = CachedDeparturesSnapshot(
        captured_at_utc=captured_at_utc,
        departures=departures,
    )


def get_snapshot_age_hhmmss(station_id: str, current_time_utc: datetime) -> str | None:
    """Return snapshot age as HH:MM:SS for logging, if present."""
    snapshot = _snapshots_by_station_id.get(station_id)
    if snapshot is None:
        return None
    elapsed_seconds = int((current_time_utc - snapshot.captured_at_utc).total_seconds())
    return _format_hhmmss(elapsed_seconds)


def _shift_departure_times(snapshot: CachedDeparturesSnapshot, current_time_utc: datetime) -> list[Departure]:
    """Shift cached departures by elapsed age."""
    snapshot_age = current_time_utc - snapshot.captured_at_utc
    return [
        replace(
            departure,
            when=departure.when - snapshot_age,
            plannedWhen=departure.plannedWhen - snapshot_age,
        )
        for departure in snapshot.departures
    ]


def get_fallback_departures(station_id: str, current_time_utc: datetime) -> list[Departure] | None:
    """Return adjusted fallback departures when the snapshot is still valid."""
    snapshot = _snapshots_by_station_id.get(station_id)
    if snapshot is None:
        return None

    shifted_departures = _shift_departure_times(snapshot, current_time_utc)
    still_valid_departures = [departure for departure in shifted_departures if departure.when >= current_time_utc]
    return still_valid_departures or None
