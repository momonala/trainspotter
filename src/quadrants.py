"""Departure grouping logic for the quadrant-based display."""

from dataclasses import dataclass
from datetime import datetime

from .datamodels import Departure
from .utils import bearing_to_cardinal
from .utils import get_direction
from .utils import get_initial_bearing


@dataclass
class QuadrantData:
    """One quadrant's display data: key, label, arrow, and upcoming departures."""

    key: str
    label: str
    arrow: str
    departures: list[tuple[int, str]]  # (minutes_until, line_name)


def compute_direction(dep: Departure) -> str | None:
    """Return direction symbol (↑ ↓ ↻ ↺ ← →) from departure bearing and line, or None if unknown."""
    if not dep.stop or not dep.stop.location or not dep.destination or not dep.destination.location:
        return None
    start = dep.stop.location
    end = dep.destination.location
    bearing = get_initial_bearing(start.latitude, start.longitude, end.latitude, end.longitude)
    return get_direction(dep.line.name, bearing_to_cardinal(bearing))


def filter_and_group(
    departures: list[Departure],
    now: datetime,
    quadrants_config: list[dict],
    min_minutes: int = 5,
    max_per_quadrant: int = 2,
) -> list[QuadrantData]:
    """Filter departures by min_minutes and group into quadrants per config.

    Args:
        departures: Raw departure list from VBB.
        now: Reference time for computing minutes-until values.
        quadrants_config: List of quadrant dicts from config.json (key, label, lines, direction).
        min_minutes: Departures with fewer remaining minutes are excluded.
        max_per_quadrant: Maximum departures kept per quadrant (sorted by soonest first).

    Returns:
        One QuadrantData per config entry, in the same order as quadrants_config.
    """
    lines_by_key = {q["key"]: set(q["lines"]) for q in quadrants_config}
    direction_by_key = {q["key"]: q["direction"] for q in quadrants_config}
    groups: dict[str, list[tuple[int, str]]] = {q["key"]: [] for q in quadrants_config}

    for dep in departures:
        line = dep.line.name
        direction = compute_direction(dep)
        if not direction:
            continue

        minutes = int((dep.when - now).total_seconds() / 60)
        if minutes <= min_minutes:
            continue

        for key, lines in lines_by_key.items():
            if line in lines and direction_by_key[key] == direction:
                groups[key].append((minutes, line))
                break

    for key in groups:
        groups[key] = sorted(groups[key], key=lambda x: x[0])[:max_per_quadrant]

    return [
        QuadrantData(key=q["key"], label=q["label"], arrow=q["direction"], departures=groups[q["key"]])
        for q in quadrants_config
    ]
