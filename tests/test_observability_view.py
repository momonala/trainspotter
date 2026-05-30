from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import MagicMock
from unittest.mock import patch

from spyglass.dashboard.schemas import LogHistogram

from src.observability.schemas import SpyglassStatus
from src.observability.view import _build_charts
from src.observability.view import _display_status
from src.observability.view import _sum_counter
from src.observability.view import _timing_summary
from src.observability.view import build_summary


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


@patch("src.observability.view.prepare_logs")
@patch("src.observability.view.build_log_histogram")
@patch("src.observability.view.resolve_rollup_minutes", return_value=15)
@patch("src.observability.view.window_hours_from", return_value=6)
@patch("src.observability.view.parse_rollup", return_value="auto")
@patch("src.observability.view.parse_window_unit", return_value="hours")
@patch("src.observability.view.parse_window_amount", return_value=6)
def test_build_summary_marks_healthy_when_display_is_fresh(
    mock_parse_amount,
    mock_parse_unit,
    mock_parse_rollup,
    mock_window_hours,
    mock_resolve_rollup,
    mock_histogram,
    mock_prepare_logs,
):
    mock_prepare_logs.return_value = []
    mock_histogram.return_value = LogHistogram(
        labels=["label1"],
        counts={"INFO": [0], "ERROR": [0], "WARNING": [0]},
        total=0,
    )

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

    summary = _timing_summary(metrics, ".request", tags={"route": "stations"})

    assert summary.count == 3
    assert summary.p50_ms == 200
    assert summary.p95_ms == 300
    assert summary.max_ms == 300


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

    assert _sum_counter(metrics, ".request", tags={"route": "stations"}) == 3


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


@patch("src.observability.view.build_log_histogram")
@patch("src.observability.view.TimeWindow")
@patch("src.observability.view.prepare_logs")
@patch("src.observability.view.resolve_rollup_minutes", return_value=5)
@patch("src.observability.view.window_hours_from", return_value=1)
@patch("src.observability.view.parse_rollup", return_value="5")
@patch("src.observability.view.parse_window_unit", return_value="hours")
@patch("src.observability.view.parse_window_amount", return_value=1)
def test_build_log_histogram_counts_by_level_and_bucket(
    mock_parse_amount,
    mock_parse_unit,
    mock_parse_rollup,
    mock_window_hours,
    mock_resolve_rollup,
    mock_prepare_logs,
    mock_time_window,
    mock_build_histogram,
):
    mock_prepare_logs.return_value = []
    mock_histogram = LogHistogram(
        labels=["label1", "label2"],
        counts={
            "INFO": [0, 1],
            "ERROR": [1, 0],
            "WARNING": [0, 1],
            "DEBUG": [0, 0],
        },
        total=3,
    )
    mock_build_histogram.return_value = mock_histogram
    mock_window = MagicMock()
    mock_time_window.from_hours.return_value = mock_window

    summary = build_summary(
        metrics=[],
        logs=[],
        window_amount=1,
        window_unit="hours",
        rollup="5",
        spyglass_status=_spyglass(),
    )

    assert len(summary.log_histogram.labels) == 2
    assert summary.log_histogram.counts["INFO"] == [0, 1]
    assert summary.log_histogram.counts["ERROR"] == [1, 0]
    assert summary.log_histogram.counts["WARNING"] == [0, 1]
    assert sum(summary.log_histogram.counts["DEBUG"]) == 0
