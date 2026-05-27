"""Fetch Spyglass data and build observability dashboard summaries."""

import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import requests

from ..config import PROJECT_NAME
from ..config import SPYGLASS_HOST
from .schemas import CacheStats
from .schemas import ChartsSummary
from .schemas import DisplayStatus
from .schemas import DisplayStatusValue
from .schemas import DisplayTotals
from .schemas import DisplayUptime
from .schemas import LatencySeries
from .schemas import LogHistogram
from .schemas import ObservabilitySummary
from .schemas import PreparedLogEntry
from .schemas import RouteSeries
from .schemas import SpyglassStatus
from .schemas import TimingSummary
from .schemas import VbbSummary
from .schemas import WindowInfo

REQUEST_TIMEOUT = 3.0
DEFAULT_METRICS_LIMIT = 5000
DEFAULT_LOGS_LIMIT = 2000
DEFAULT_WINDOW_AMOUNT = 6
DEFAULT_ROLLUP = "10"
TARGET_CHART_BUCKETS = 24
MAX_WINDOW_HOURS = 8640
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DISPLAY_STATUS_LOOKBACK_MINUTES = 5
DISPLAY_STATUS_LABELS: dict[DisplayStatusValue, str] = {
    "fresh": "Fresh — display is serving live VBB data",
    "stale": "Stale — display is serving a cached snapshot",
    "degraded": "Degraded — display could not serve data",
    "unknown": "Unknown — Spyglass unreachable",
}

WINDOW_UNIT_HOURS = {
    "hours": 1,
    "days": 24,
    "weeks": 168,
    "months": 720,
}
VALID_WINDOW_UNITS = frozenset(WINDOW_UNIT_HOURS)
VALID_ROLLUP_VALUES = frozenset({"auto", "1", "2", "5", "10", "15", "30", "60", "120", "360", "720", "1440"})

# Longest suffixes first so `.display.fresh` wins over shorter accidental matches.
METRIC_SUFFIXES = (
    ".display.snapshot_age_seconds",
    ".display.no_snapshot",
    ".display.fallback",
    ".display.fresh",
    ".response.502",
    ".vbb.cache_hit",
    ".vbb.cache_miss",
    ".vbb.fetch",
    ".vbb.error",
    ".request",
)

SPYGLASS_LOG_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)? " r"\w+ " r"\[([^\]]+)\] " r"\S+ " r"(.*)$",
    re.DOTALL,
)


def _normalize_host(host: str) -> str:
    if host.startswith(("http://", "https://")):
        return host
    return f"http://{host}"


SPYGLASS_BASE = _normalize_host(SPYGLASS_HOST)


@dataclass(frozen=True)
class _TimeWindow:
    """Fixed-size time buckets for chart and histogram aggregation."""

    start: datetime
    bucket_minutes: int
    bucket_count: int

    @classmethod
    def from_hours(cls, window_hours: int, now: datetime, rollup_minutes: int) -> "_TimeWindow":
        bucket_count = max(1, int(window_hours * 60 / rollup_minutes))
        return cls(
            start=now - timedelta(hours=window_hours),
            bucket_minutes=rollup_minutes,
            bucket_count=bucket_count,
        )

    def labels(self, window_hours: int) -> list[str]:
        return _bucket_labels(self.start, self.bucket_minutes, self.bucket_count, window_hours)


def parse_window_amount(amount: int | None) -> int:
    """Clamp window amount to at least 1."""
    return max(1, amount if amount is not None else DEFAULT_WINDOW_AMOUNT)


def parse_window_unit(unit: str | None) -> str:
    """Return a supported window unit, defaulting to hours."""
    if unit in VALID_WINDOW_UNITS:
        return unit
    return "hours"


def window_hours_from(amount: int, unit: str) -> int:
    """Convert amount + unit to hours, capped at 12 months."""
    return min(parse_window_amount(amount) * WINDOW_UNIT_HOURS[unit], MAX_WINDOW_HOURS)


def parse_rollup(rollup: str | None) -> str:
    """Return a supported rollup value, defaulting to DEFAULT_ROLLUP."""
    if rollup in VALID_ROLLUP_VALUES:
        return rollup
    return DEFAULT_ROLLUP


def resolve_rollup_minutes(rollup: str, window_hours: int) -> int:
    """Resolve rollup query param to bucket width in minutes."""
    if rollup == "auto":
        return _bucket_minutes_for_window(window_hours)
    return int(rollup)


def fetch_spyglass_status() -> SpyglassStatus:
    """Return Spyglass server reachability."""
    try:
        response = requests.get(f"{SPYGLASS_BASE}/status", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return SpyglassStatus(reachable=True, status=response.json())
    except requests.RequestException as error:
        return SpyglassStatus(reachable=False, error=str(error))


def _fetch_spyglass(resource: str, since: datetime, limit: int) -> list[dict]:
    response = requests.get(
        f"{SPYGLASS_BASE}/{resource}",
        params={
            "project": PROJECT_NAME,
            "from": since.astimezone(timezone.utc).isoformat(),
            "limit": limit,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_metrics(since: datetime, limit: int = DEFAULT_METRICS_LIMIT) -> list[dict]:
    """Query raw metric points from Spyglass."""
    return _fetch_spyglass("metrics", since, limit)


def fetch_logs(since: datetime, limit: int = DEFAULT_LOGS_LIMIT) -> list[dict]:
    """Query recent log entries from Spyglass."""
    return _fetch_spyglass("logs", since, limit)


def _prepare_logs(logs: list[dict]) -> list[PreparedLogEntry]:
    """Parse Spyglass log entries into structured rows for the dashboard."""
    prepared: list[PreparedLogEntry] = []
    for entry in logs:
        raw_message = entry.get("message") or ""
        match = SPYGLASS_LOG_PREFIX.match(raw_message)
        if match:
            function = match.group(1)
            message = match.group(2)
        else:
            function = None
            message = raw_message
        prepared.append(
            PreparedLogEntry(
                timestamp=entry.get("timestamp"),
                level=entry.get("level"),
                logger_name=entry.get("logger_name"),
                function=function,
                message=message,
            )
        )
    return prepared


def _metric_suffix(name: str) -> str | None:
    """Return the known metric suffix for a Spyglass metric name, if any."""
    return next((suffix for suffix in METRIC_SUFFIXES if name.endswith(suffix)), None)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = min(int(len(sorted_values) * pct / 100), len(sorted_values) - 1)
    return sorted_values[index]


def _points(
    metrics: list[dict],
    suffix: str,
    metric_type: str | None = None,
    route: str | None = None,
) -> list[dict]:
    matched: list[dict] = []
    for point in metrics:
        if _metric_suffix(point["name"]) != suffix:
            continue
        if metric_type is not None and point["metric_type"] != metric_type:
            continue
        if route is not None:
            tags = point.get("tags") or {}
            if tags.get("route") != route:
                continue
        matched.append(point)
    return matched


def _sum_counter(metrics: list[dict], suffix: str, route: str | None = None) -> float:
    return sum(point["value"] for point in _points(metrics, suffix, "counter", route))


def _timing_summary(metrics: list[dict], suffix: str, route: str | None = None) -> TimingSummary:
    values = [point["value"] for point in _points(metrics, suffix, "timing", route)]
    if not values:
        return TimingSummary(count=0, p50_ms=None, p95_ms=None, max_ms=None)
    return TimingSummary(
        count=len(values),
        p50_ms=_percentile(values, 50),
        p95_ms=_percentile(values, 95),
        max_ms=max(values),
    )


def _latest_gauge(metrics: list[dict], suffix: str) -> float | None:
    points = _points(metrics, suffix, "gauge")
    if not points:
        return None
    latest = max(points, key=lambda point: _parse_metric_time(point["timestamp"]))
    return latest["value"]


def _outcome_status_for_point(point: dict) -> DisplayStatusValue | None:
    """Map a display-outcome counter to fresh, stale, or degraded."""
    suffix = _metric_suffix(point["name"])
    tags = point.get("tags") or {}
    if suffix == ".display.no_snapshot":
        return "degraded"
    if suffix == ".response.502" and tags.get("route") == "display_data":
        return "degraded"
    if suffix == ".display.fallback":
        return "stale"
    if suffix == ".display.fresh":
        return "fresh"
    return None


def _display_status(metrics: list[dict], now: datetime, spyglass_reachable: bool) -> DisplayStatus:
    """Infer current display serving mode from the most recent display outcome metric."""
    if not spyglass_reachable:
        return DisplayStatus(status="unknown", label=DISPLAY_STATUS_LABELS["unknown"])

    cutoff = now - timedelta(minutes=DISPLAY_STATUS_LOOKBACK_MINUTES)
    latest: tuple[datetime, DisplayStatusValue, str] | None = None

    for point in metrics:
        if point.get("metric_type") != "counter" or point["value"] <= 0:
            continue
        status = _outcome_status_for_point(point)
        if status is None:
            continue
        parsed_time = _parse_metric_time(point["timestamp"])
        if parsed_time < cutoff:
            continue
        if latest is None or parsed_time > latest[0]:
            latest = (parsed_time, status, point["timestamp"])

    if latest is None:
        return DisplayStatus(status="fresh", label=DISPLAY_STATUS_LABELS["fresh"])

    _, status, based_on = latest
    return DisplayStatus(status=status, label=DISPLAY_STATUS_LABELS[status], based_on=based_on)


def _build_display_totals(metrics: list[dict], window_hours: int, now: datetime) -> DisplayTotals:
    return DisplayTotals(
        fresh=_sum_counter(metrics, ".display.fresh"),
        fallback=_sum_counter(metrics, ".display.fallback"),
        no_snapshot=_sum_counter(metrics, ".display.no_snapshot"),
        failed_responses=_sum_counter(metrics, ".response.502", route="display_data"),
        snapshot_age_seconds=_latest_gauge(metrics, ".display.snapshot_age_seconds"),
        uptime=_build_display_uptime(metrics, now=now, window_hours=window_hours),
    )


def _build_display_uptime(
    metrics: list[dict], now: datetime | None = None, window_hours: int = DEFAULT_WINDOW_AMOUNT
) -> DisplayUptime:
    """Calculate time-weighted display uptime percentages from outcome transitions."""
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=window_hours)

    events: list[tuple[datetime, DisplayStatusValue]] = []
    for point in metrics:
        if point.get("metric_type") != "counter" or point["value"] <= 0:
            continue
        status = _outcome_status_for_point(point)
        if status is None:
            continue
        timestamp = _parse_metric_time(point["timestamp"])
        if timestamp < window_start or timestamp > window_end:
            continue
        events.append((timestamp, status))

    events.sort(key=lambda item: item[0])

    durations = {"fresh": 0.0, "stale": 0.0, "degraded": 0.0, "unknown": 0.0}
    current_status: DisplayStatusValue = "unknown"
    cursor = window_start

    for event_time, event_status in events:
        elapsed_seconds = (event_time - cursor).total_seconds()
        if elapsed_seconds > 0:
            durations[current_status] += elapsed_seconds
        current_status = event_status
        cursor = event_time

    tail_seconds = (window_end - cursor).total_seconds()
    if tail_seconds > 0:
        durations[current_status] += tail_seconds

    total_seconds = max(0.0, (window_end - window_start).total_seconds())
    if total_seconds == 0:
        return DisplayUptime(
            fresh_seconds=0.0,
            stale_seconds=0.0,
            degraded_seconds=0.0,
            unknown_seconds=0.0,
            fresh_pct=0.0,
            stale_pct=0.0,
            degraded_pct=0.0,
            unknown_pct=0.0,
            outcome_events=0,
        )

    return DisplayUptime(
        fresh_seconds=durations["fresh"],
        stale_seconds=durations["stale"],
        degraded_seconds=durations["degraded"],
        unknown_seconds=durations["unknown"],
        fresh_pct=durations["fresh"] / total_seconds * 100,
        stale_pct=durations["stale"] / total_seconds * 100,
        degraded_pct=durations["degraded"] / total_seconds * 100,
        unknown_pct=durations["unknown"] / total_seconds * 100,
        outcome_events=len(events),
    )


def _build_vbb_summary(metrics: list[dict]) -> VbbSummary:
    cache_hits = _sum_counter(metrics, ".vbb.cache_hit")
    cache_misses = _sum_counter(metrics, ".vbb.cache_miss")
    cache_total = cache_hits + cache_misses
    return VbbSummary(
        errors=_sum_counter(metrics, ".vbb.error"),
        fetch_latency=_timing_summary(metrics, ".vbb.fetch"),
        cache=CacheStats(
            hits=cache_hits,
            misses=cache_misses,
            hit_rate_pct=(cache_hits / cache_total * 100) if cache_total else None,
        ),
    )


def _bucket_minutes_for_window(window_hours: int) -> int:
    """Pick a bucket width that yields roughly TARGET_CHART_BUCKETS bars."""
    raw = max(1, int(window_hours * 60 / TARGET_CHART_BUCKETS))
    for candidate in (1, 5, 15, 30, 60, 120, 360, 720, 1440):
        if raw <= candidate:
            return candidate
    return 1440


def _parse_metric_time(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _bucket_index(timestamp: datetime, window: _TimeWindow) -> int | None:
    elapsed_minutes = (timestamp - window.start).total_seconds() / 60
    if elapsed_minutes < 0:
        return None
    index = int(elapsed_minutes // window.bucket_minutes)
    if index >= window.bucket_count:
        return None
    return index


def _bucket_labels(window_start: datetime, bucket_minutes: int, bucket_count: int, window_hours: int) -> list[str]:
    labels: list[str] = []
    for index in range(bucket_count):
        start = window_start + timedelta(minutes=index * bucket_minutes)
        if window_hours <= 24:
            labels.append(start.strftime("%H:%M"))
        else:
            labels.append(start.strftime("%m-%d %H:%M"))
    return labels


def _timing_p50_by_bucket(
    metrics: list[dict],
    suffix: str,
    window: _TimeWindow,
    route: str | None = None,
) -> list[float | None]:
    buckets: list[list[float]] = [[] for _ in range(window.bucket_count)]
    for point in _points(metrics, suffix, "timing", route):
        index = _bucket_index(_parse_metric_time(point["timestamp"]), window)
        if index is not None:
            buckets[index].append(point["value"])
    return [_percentile(values, 50) if values else None for values in buckets]


def _counter_series_by_bucket(
    metrics: list[dict],
    suffix: str,
    window: _TimeWindow,
    route: str | None = None,
) -> list[float]:
    series = [0.0] * window.bucket_count
    for point in _points(metrics, suffix, "counter", route):
        index = _bucket_index(_parse_metric_time(point["timestamp"]), window)
        if index is not None:
            series[index] += point["value"]
    return series


def _cache_hit_rate_by_bucket(metrics: list[dict], window: _TimeWindow) -> list[float | None]:
    hits = _counter_series_by_bucket(metrics, ".vbb.cache_hit", window)
    misses = _counter_series_by_bucket(metrics, ".vbb.cache_miss", window)
    rates: list[float | None] = []
    for hit_count, miss_count in zip(hits, misses, strict=True):
        total = hit_count + miss_count
        rates.append((hit_count / total * 100) if total else None)
    return rates


def _build_charts(metrics: list[dict], window_hours: int, now: datetime, rollup_minutes: int) -> ChartsSummary:
    """Build time-series data for dashboard charts."""
    window = _TimeWindow.from_hours(window_hours, now, rollup_minutes)

    return ChartsSummary(
        bucket_minutes=window.bucket_minutes,
        labels=window.labels(window_hours),
        requests_by_route=RouteSeries(
            stations=_counter_series_by_bucket(metrics, ".request", window, route="stations"),
            display_data=_counter_series_by_bucket(metrics, ".request", window, route="display_data"),
        ),
        latency_p50_ms=LatencySeries(
            stations=_timing_p50_by_bucket(metrics, ".request", window, route="stations"),
            display_data=_timing_p50_by_bucket(metrics, ".request", window, route="display_data"),
            vbb_fetch=_timing_p50_by_bucket(metrics, ".vbb.fetch", window),
        ),
        vbb_errors=_counter_series_by_bucket(metrics, ".vbb.error", window),
        cache_hit_rate_pct=_cache_hit_rate_by_bucket(metrics, window),
    )


def _build_log_histogram(
    logs: list[PreparedLogEntry], window_hours: int, now: datetime, rollup_minutes: int
) -> LogHistogram:
    """Count log entries per level in each time bucket."""
    window = _TimeWindow.from_hours(window_hours, now, rollup_minutes)
    counts = {level: [0] * window.bucket_count for level in LOG_LEVELS}

    for entry in logs:
        if not entry.timestamp or entry.level not in counts:
            continue
        index = _bucket_index(_parse_metric_time(entry.timestamp), window)
        if index is not None:
            counts[entry.level][index] += 1

    return LogHistogram(labels=window.labels(window_hours), by_level=counts)


def build_summary(
    metrics: list[dict],
    logs: list[dict],
    window_amount: int,
    window_unit: str,
    rollup: str,
    spyglass_status: SpyglassStatus,
) -> ObservabilitySummary:
    """Aggregate Spyglass points into dashboard sections."""
    now = datetime.now(timezone.utc)
    amount = parse_window_amount(window_amount)
    unit = parse_window_unit(window_unit)
    rollup_value = parse_rollup(rollup)
    window_hours = window_hours_from(amount, unit)
    rollup_minutes = resolve_rollup_minutes(rollup_value, window_hours)
    prepared_logs = _prepare_logs(logs)

    return ObservabilitySummary(
        generated_at=now.isoformat(),
        window=WindowInfo(
            amount=amount,
            unit=unit,
            hours=window_hours,
            rollup_minutes=rollup_minutes,
        ),
        display_status=_display_status(metrics, now, spyglass_status.reachable),
        spyglass=spyglass_status,
        charts=_build_charts(metrics, window_hours, now, rollup_minutes),
        display=_build_display_totals(metrics, window_hours, now),
        vbb=_build_vbb_summary(metrics),
        log_histogram=_build_log_histogram(prepared_logs, window_hours, now, rollup_minutes),
        logs=prepared_logs,
    )
