"""Fetch Spyglass data and build the Trainspotter observability dashboard summary.

Generic math (bucketing, percentiles, series) lives in spyglass.dashboard.aggregate.
HTTP fetching lives in SpyglassQueryClient.
This file contains only Trainspotter-specific assembly: metric suffix disambiguation,
display-state classification, and section builders that compose aggregate results into
the typed ObservabilitySummary schema.
"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from spyglass.client.query import SpyglassQueryClient
from spyglass.dashboard.aggregate import TimeWindow
from spyglass.dashboard.aggregate import build_log_histogram
from spyglass.dashboard.aggregate import compute_state_uptime
from spyglass.dashboard.aggregate import counter_series
from spyglass.dashboard.aggregate import latest_gauge
from spyglass.dashboard.aggregate import parse_metric_time
from spyglass.dashboard.aggregate import parse_rollup
from spyglass.dashboard.aggregate import parse_window_amount
from spyglass.dashboard.aggregate import parse_window_unit
from spyglass.dashboard.aggregate import prepare_logs
from spyglass.dashboard.aggregate import ratio_series
from spyglass.dashboard.aggregate import resolve_rollup_minutes
from spyglass.dashboard.aggregate import timing_p50_series
from spyglass.dashboard.aggregate import window_hours_from

from ..config import PROJECT_NAME
from ..config import SPYGLASS_HOST
from .config import DISPLAY_STATES
from .config import DISPLAY_STATUS_LOOKBACK_MINUTES
from .config import METRIC_SUFFIXES
from .schemas import CacheStats
from .schemas import ChartsSummary
from .schemas import DisplayStatus
from .schemas import DisplayStatusValue
from .schemas import DisplayTotals
from .schemas import DisplayUptime
from .schemas import LatencySeries
from .schemas import ObservabilitySummary
from .schemas import RouteSeries
from .schemas import SpyglassStatus
from .schemas import TimingSummary
from .schemas import VbbSummary
from .schemas import WindowInfo

_client = SpyglassQueryClient(host=SPYGLASS_HOST, project=PROJECT_NAME)

DISPLAY_STATUS_LABELS: dict[DisplayStatusValue, str] = {
    "fresh": "Fresh — display is serving live VBB data",
    "stale": "Stale — display is serving a cached snapshot",
    "degraded": "Degraded — display could not serve data",
    "unknown": "Unknown — Spyglass unreachable",
}


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware (UTC). Assume naive datetimes are UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# HTTP helpers (thin wrappers over SpyglassQueryClient)


def fetch_spyglass_status() -> SpyglassStatus:
    result = _client.status()
    if result is None:
        return SpyglassStatus(reachable=False, error="unreachable")
    return SpyglassStatus(reachable=True, status=result)


def fetch_metrics(since: datetime) -> list[dict]:
    return _client.fetch_metrics(since)


def fetch_logs(since: datetime) -> list[dict]:
    return _client.fetch_logs(since)


# Trainspotter metric classification


def _metric_suffix(name: str) -> str | None:
    """Return the known Trainspotter suffix for a metric name, longest match first."""
    return next((s for s in METRIC_SUFFIXES if name.endswith(s)), None)


def _points(
    metrics: list[dict],
    suffix: str,
    metric_type: str | None = None,
    tags: dict | None = None,
) -> list[dict]:
    """Filter metrics using Trainspotter suffix disambiguation + optional tags."""
    matched = []
    for point in metrics:
        if _metric_suffix(point["name"]) != suffix:
            continue
        if metric_type is not None and point["metric_type"] != metric_type:
            continue
        if tags:
            point_tags = point.get("tags") or {}
            if not all(point_tags.get(k) == v for k, v in tags.items()):
                continue
        matched.append(point)
    return matched


def _sum_counter(metrics: list[dict], suffix: str, tags: dict | None = None) -> float:
    return sum(p["value"] for p in _points(metrics, suffix, "counter", tags))


def _timing_summary(metrics: list[dict], suffix: str, tags: dict | None = None) -> TimingSummary:
    values = [p["value"] for p in _points(metrics, suffix, "timing", tags)]
    if not values:
        return TimingSummary(count=0, p50_ms=None, p95_ms=None, max_ms=None)
    sorted_v = sorted(values)
    p50_idx = min(int(len(sorted_v) * 50 / 100), len(sorted_v) - 1)
    p95_idx = min(int(len(sorted_v) * 95 / 100), len(sorted_v) - 1)
    return TimingSummary(
        count=len(values),
        p50_ms=sorted_v[p50_idx],
        p95_ms=sorted_v[p95_idx],
        max_ms=max(values),
    )


# Display status (current state badge — 5-minute lookback)


def _outcome_status_for_point(point: dict) -> DisplayStatusValue | None:
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
        ts = _ensure_utc(parse_metric_time(point["timestamp"]))
        if ts < cutoff:
            continue
        if latest is None or ts > latest[0]:
            latest = (ts, status, point["timestamp"])

    if latest is None:
        return DisplayStatus(status="fresh", label=DISPLAY_STATUS_LABELS["fresh"])

    _, status, based_on = latest
    return DisplayStatus(status=status, label=DISPLAY_STATUS_LABELS[status], based_on=based_on)


# Section builders (Trainspotter-specific composition)


def _build_display_uptime(metrics: list[dict], now: datetime, window_hours: int) -> DisplayUptime:
    window_end = now
    window_start = window_end - timedelta(hours=window_hours)

    events: list[tuple[datetime, str]] = []
    for point in metrics:
        if point.get("metric_type") != "counter" or point["value"] <= 0:
            continue
        status = _outcome_status_for_point(point)
        if status is None:
            continue
        ts = _ensure_utc(parse_metric_time(point["timestamp"]))
        if window_start <= ts <= window_end:
            events.append((ts, status))

    result = compute_state_uptime(events, window_start, window_end, DISPLAY_STATES)
    return DisplayUptime(
        fresh_seconds=result.seconds.get("fresh", 0.0),
        stale_seconds=result.seconds.get("stale", 0.0),
        degraded_seconds=result.seconds.get("degraded", 0.0),
        unknown_seconds=result.seconds.get("unknown", 0.0),
        fresh_pct=result.pcts.get("fresh", 0.0),
        stale_pct=result.pcts.get("stale", 0.0),
        degraded_pct=result.pcts.get("degraded", 0.0),
        unknown_pct=result.pcts.get("unknown", 0.0),
        outcome_events=result.event_count,
    )


def _build_display_totals(metrics: list[dict], window_hours: int, now: datetime) -> DisplayTotals:
    return DisplayTotals(
        fresh=_sum_counter(metrics, ".display.fresh"),
        fallback=_sum_counter(metrics, ".display.fallback"),
        no_snapshot=_sum_counter(metrics, ".display.no_snapshot"),
        failed_responses=_sum_counter(metrics, ".response.502", tags={"route": "display_data"}),
        snapshot_age_seconds=latest_gauge(metrics, ".display.snapshot_age_seconds"),
        uptime=_build_display_uptime(metrics, now=now, window_hours=window_hours),
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


def _build_charts(metrics: list[dict], window_hours: int, now: datetime, rollup_minutes: int) -> ChartsSummary:
    window = TimeWindow.from_hours(window_hours, now, rollup_minutes)
    hits = counter_series(metrics, ".vbb.cache_hit", window)
    misses = counter_series(metrics, ".vbb.cache_miss", window)
    totals = [h + m for h, m in zip(hits, misses)]
    return ChartsSummary(
        bucket_minutes=window.bucket_minutes,
        labels=window.labels(),
        requests_by_route=RouteSeries(
            stations=counter_series(metrics, ".request", window, tags={"route": "stations"}),
            display_data=counter_series(metrics, ".request", window, tags={"route": "display_data"}),
        ),
        latency_p50_ms=LatencySeries(
            stations=timing_p50_series(metrics, ".request", window, tags={"route": "stations"}),
            display_data=timing_p50_series(metrics, ".request", window, tags={"route": "display_data"}),
            vbb_fetch=timing_p50_series(metrics, ".vbb.fetch", window),
        ),
        vbb_errors=counter_series(metrics, ".vbb.error", window),
        cache_hit_rate_pct=ratio_series(hits, totals),
    )


# Public entry point


def build_summary(
    metrics: list[dict],
    logs: list[dict],
    window_amount: int,
    window_unit: str,
    rollup: str,
    spyglass_status: SpyglassStatus,
) -> ObservabilitySummary:
    """Aggregate Spyglass points into typed dashboard sections."""
    now = datetime.now(timezone.utc)
    amount = parse_window_amount(window_amount)
    unit = parse_window_unit(window_unit)
    rollup_value = parse_rollup(rollup)
    window_hours = window_hours_from(amount, unit)
    rollup_minutes = resolve_rollup_minutes(rollup_value, window_hours)
    prepared_logs = prepare_logs(logs)

    return ObservabilitySummary(
        generated_at=now.isoformat(),
        window=WindowInfo(amount=amount, unit=unit, hours=window_hours, rollup_minutes=rollup_minutes),
        display_status=_display_status(metrics, now, spyglass_status.reachable),
        spyglass=spyglass_status,
        charts=_build_charts(metrics, window_hours, now, rollup_minutes),
        display=_build_display_totals(metrics, window_hours, now),
        vbb=_build_vbb_summary(metrics),
        log_histogram=build_log_histogram(
            prepared_logs,
            TimeWindow.from_hours(window_hours, now, rollup_minutes),
        ),
        logs=prepared_logs,
    )
