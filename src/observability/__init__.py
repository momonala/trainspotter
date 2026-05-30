"""Observability dashboard aggregation for Trainspotter."""

from spyglass.dashboard.aggregate import DEFAULT_ROLLUP
from spyglass.dashboard.aggregate import DEFAULT_WINDOW_AMOUNT
from spyglass.dashboard.aggregate import parse_rollup
from spyglass.dashboard.aggregate import parse_window_amount
from spyglass.dashboard.aggregate import parse_window_unit
from spyglass.dashboard.aggregate import window_hours_from

from .schemas import ObservabilitySummary
from .schemas import SpyglassStatus
from .view import build_summary
from .view import fetch_logs
from .view import fetch_metrics
from .view import fetch_spyglass_status

__all__ = [
    "DEFAULT_ROLLUP",
    "DEFAULT_WINDOW_AMOUNT",
    "ObservabilitySummary",
    "SpyglassStatus",
    "build_summary",
    "fetch_logs",
    "fetch_metrics",
    "fetch_spyglass_status",
    "parse_rollup",
    "parse_window_amount",
    "parse_window_unit",
    "window_hours_from",
]
