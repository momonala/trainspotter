from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from src.observability.schemas import PreparedLogEntry
from src.observability.schemas import SpyglassStatus
from src.observability.view import DEFAULT_ROLLUP
from src.observability.view import _build_charts
from src.observability.view import _build_log_histogram
from src.observability.view import _display_status
from src.observability.view import _prepare_logs
from src.observability.view import _sum_counter
from src.observability.view import _timing_summary
from src.observability.view import build_summary
from src.observability.view import parse_rollup
from src.observability.view import parse_window_unit
from src.observability.view import resolve_rollup_minutes
from src.observability.view import window_hours_from


def _spyglass(reachable: bool = True) -> SpyglassStatus:
    if reachable:
        return SpyglassStatus(reachable=True, status={"status": "ok"})
    return SpyglassStatus(reachable=False, error="down")


def _point(name: str, metric_type: str, value: float, tags: dict | None = None) -> dict:
    return {
        "timestamp": "2026-05-27T10:00:00Z",
        "name": name,
        "metric_type": metric_type,
        "value": value,
        "tags": tags,
    }


def test_build_summary_marks_healthy_when_display_is_fresh():
    metrics = [
        _point("trainspotter.wrapper.request", "counter", 5, {"route": "display_data"}),
        _point("trainspotter._fetch_display_departures.display.fresh", "counter", 12),
        _point("trainspotter._fetch_departures_from_vbb.vbb.fetch", "timing", 120),
        _point("trainspotter.get_inbound_trains_cached.vbb.cache_hit", "counter", 8),
        _point("trainspotter.get_inbound_trains_cached.vbb.cache_miss", "counter", 2),
    ]
    summary = build_summary(
        metrics=metrics,
        logs=[],
        window_amount=6,
        window_unit="hours",
        rollup="auto",
        spyglass_status=_spyglass(),
    )

    assert summary.display_status.status == "fresh"
    assert summary.display.fresh == 12
    assert summary.vbb.cache.hit_rate_pct == 80.0
    assert summary.window.model_dump() == {"amount": 6, "unit": "hours", "hours": 6, "rollup_minutes": 15}
    assert summary.logs == []
    assert summary.charts.labels
    assert summary.charts.latency_p50_ms.vbb_fetch is not None


def _recent_point(name: str, metric_type: str, value: float, tags: dict | None = None) -> dict:
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "timestamp": recent,
        "name": name,
        "metric_type": metric_type,
        "value": value,
        "tags": tags,
    }


def test_build_summary_marks_stale_on_recent_fallback_metric():
    summary = build_summary(
        metrics=[_recent_point("trainspotter._fetch_display_departures.display.fallback", "counter", 1)],
        logs=[],
        window_amount=1,
        window_unit="hours",
        rollup="auto",
        spyglass_status=_spyglass(),
    )

    assert summary.display_status.status == "stale"


def test_build_summary_marks_degraded_on_recent_no_snapshot_metric():
    summary = build_summary(
        metrics=[_recent_point("trainspotter._fetch_display_departures.display.no_snapshot", "counter", 1)],
        logs=[],
        window_amount=1,
        window_unit="hours",
        rollup="auto",
        spyglass_status=_spyglass(),
    )

    assert summary.display_status.status == "degraded"


def test_build_summary_ignores_historical_fallback_metrics_without_recent_logs():
    metrics = [
        _point("trainspotter._fetch_display_departures.display.fresh", "counter", 3),
        _point("trainspotter._fetch_display_departures.display.fallback", "counter", 2),
    ]
    summary = build_summary(
        metrics=metrics,
        logs=[],
        window_amount=1,
        window_unit="hours",
        rollup="auto",
        spyglass_status=_spyglass(),
    )

    assert summary.display_status.status == "fresh"
    assert summary.display.fallback == 2


def test_build_summary_marks_unknown_when_spyglass_unreachable():
    summary = build_summary(
        metrics=[],
        logs=[],
        window_amount=1,
        window_unit="hours",
        rollup="auto",
        spyglass_status=_spyglass(reachable=False),
    )

    assert summary.display_status.status == "unknown"


def test_timing_summary_computes_percentiles():
    metrics = [
        _point("trainspotter.wrapper.request", "timing", 100, {"route": "stations"}),
        _point("trainspotter.wrapper.request", "timing", 200, {"route": "stations"}),
        _point("trainspotter.wrapper.request", "timing", 300, {"route": "stations"}),
    ]

    summary = _timing_summary(metrics, ".request", route="stations")

    assert summary.count == 3
    assert summary.p50_ms == 200
    assert summary.p95_ms == 300
    assert summary.max_ms == 300


def test_prepare_logs_parses_spyglass_message_format():
    logs = [
        {
            "timestamp": "2026-05-27T10:00:00Z",
            "level": "INFO",
            "logger_name": "src.app",
            "message": "2026-05-27 10:00:00,123 INFO [api_display_data] src.app departures refreshed",
        },
        {
            "timestamp": "2026-05-27T10:01:00Z",
            "level": "WARNING",
            "logger_name": "src.app",
            "message": "plain message without prefix",
        },
    ]

    prepared = _prepare_logs(logs)

    assert prepared[0] == PreparedLogEntry(
        timestamp="2026-05-27T10:00:00Z",
        level="INFO",
        logger_name="src.app",
        function="api_display_data",
        message="departures refreshed",
    )
    assert prepared[1].function is None
    assert prepared[1].message == "plain message without prefix"


def test_display_status_uses_most_recent_outcome_metric():
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    metrics = [
        _point("trainspotter._fetch_display_departures.display.fallback", "counter", 1),
        {
            "timestamp": "2026-05-27T11:59:30Z",
            "name": "trainspotter._fetch_display_departures.display.no_snapshot",
            "metric_type": "counter",
            "value": 1,
            "tags": None,
        },
    ]
    metrics[0]["timestamp"] = "2026-05-27T11:58:00Z"

    status = _display_status(metrics, now, spyglass_reachable=True)

    assert status.status == "degraded"
    assert status.based_on == "2026-05-27T11:59:30Z"


def test_display_status_ignores_old_outcome_metrics_outside_lookback():
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    metrics = [
        {
            "timestamp": "2026-05-27T10:00:00Z",
            "name": "trainspotter._fetch_display_departures.display.fallback",
            "metric_type": "counter",
            "value": 1,
            "tags": None,
        }
    ]

    status = _display_status(metrics, now, spyglass_reachable=True)

    assert status.status == "fresh"


def test_sum_counter_filters_by_route_tag():
    metrics = [
        _point("trainspotter.wrapper.request", "counter", 3, {"route": "stations"}),
        _point("trainspotter.wrapper.request", "counter", 7, {"route": "display_data"}),
    ]

    assert _sum_counter(metrics, ".request", route="stations") == 3


def test_build_charts_buckets_metrics_by_time():
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    metrics = [
        {
            "timestamp": "2026-05-27T11:50:00Z",
            "name": "trainspotter.wrapper.request",
            "metric_type": "counter",
            "value": 2,
            "tags": {"route": "stations"},
        },
        {
            "timestamp": "2026-05-27T11:55:00Z",
            "name": "trainspotter.wrapper.request",
            "metric_type": "counter",
            "value": 3,
            "tags": {"route": "stations"},
        },
        {
            "timestamp": "2026-05-27T11:52:00Z",
            "name": "trainspotter._fetch_departures_from_vbb.vbb.fetch",
            "metric_type": "timing",
            "value": 100,
            "tags": None,
        },
        {
            "timestamp": "2026-05-27T11:58:00Z",
            "name": "trainspotter._fetch_departures_from_vbb.vbb.fetch",
            "metric_type": "timing",
            "value": 300,
            "tags": None,
        },
    ]

    charts = _build_charts(metrics, window_hours=1, now=now, rollup_minutes=5)

    assert charts.bucket_minutes == 5
    assert sum(charts.requests_by_route.stations) == 5
    assert charts.latency_p50_ms.vbb_fetch[-2] == 100
    assert charts.latency_p50_ms.vbb_fetch[-1] == 300


def test_build_log_histogram_counts_by_level_and_bucket():
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    logs = _prepare_logs(
        [
            {"timestamp": "2026-05-27T11:50:00Z", "level": "INFO", "logger_name": "src.app", "message": "a"},
            {"timestamp": "2026-05-27T11:50:00Z", "level": "ERROR", "logger_name": "src.app", "message": "b"},
            {"timestamp": "2026-05-27T11:55:00Z", "level": "WARNING", "logger_name": "src.app", "message": "c"},
        ]
    )

    histogram = _build_log_histogram(logs, window_hours=1, now=now, rollup_minutes=5)

    assert len(histogram.labels) == 12
    assert histogram.by_level["INFO"][-2] == 1
    assert histogram.by_level["ERROR"][-2] == 1
    assert histogram.by_level["WARNING"][-1] == 1
    assert sum(histogram.by_level["DEBUG"]) == 0


@pytest.mark.parametrize(
    ("amount", "unit", "expected_hours"),
    [
        (6, "hours", 6),
        (2, "days", 48),
        (1, "weeks", 168),
        (3, "months", 2160),
        (0, "hours", 1),
        (20, "months", 8640),
    ],
)
def test_window_hours_from(amount, unit, expected_hours):
    assert window_hours_from(amount, unit) == expected_hours


def test_parse_window_unit_defaults_to_hours():
    assert parse_window_unit("invalid") == "hours"


def test_parse_rollup_defaults_to_default_rollup():
    assert parse_rollup("invalid") == DEFAULT_ROLLUP


def test_resolve_rollup_minutes_uses_explicit_bucket():
    assert resolve_rollup_minutes("30", window_hours=6) == 30


def test_resolve_rollup_minutes_auto_uses_window_logic():
    assert resolve_rollup_minutes("auto", window_hours=6) == 15
