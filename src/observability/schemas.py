"""Pydantic models for the Trainspotter observability dashboard API.

Generic types (PreparedLogEntry, LogHistogram, WindowInfo) are imported from
spyglass.dashboard.schemas so they stay in sync with the library definition.
Trainspotter-specific types (display/vbb sections, ObservabilitySummary) live here.
"""

from typing import Literal

from pydantic import BaseModel
from spyglass.dashboard.schemas import LogHistogram
from spyglass.dashboard.schemas import PreparedLogEntry
from spyglass.dashboard.schemas import WindowInfo

__all__ = [
    # re-exported from spyglass for backwards compat
    "LogHistogram",
    "PreparedLogEntry",
    "WindowInfo",
    # Trainspotter-specific
    "CacheStats",
    "ChartsSummary",
    "DisplayStatus",
    "DisplayStatusValue",
    "DisplayTotals",
    "DisplayUptime",
    "LatencySeries",
    "ObservabilitySummary",
    "RouteSeries",
    "SpyglassStatus",
    "TimingSummary",
    "VbbSummary",
]

DisplayStatusValue = Literal["fresh", "stale", "degraded", "unknown"]


class DisplayStatus(BaseModel):
    status: DisplayStatusValue
    label: str
    based_on: str | None = None


class TimingSummary(BaseModel):
    count: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class CacheStats(BaseModel):
    hits: float
    misses: float
    hit_rate_pct: float | None


class VbbSummary(BaseModel):
    errors: float
    fetch_latency: TimingSummary
    cache: CacheStats


class DisplayUptime(BaseModel):
    fresh_seconds: float
    stale_seconds: float
    degraded_seconds: float
    unknown_seconds: float
    fresh_pct: float
    stale_pct: float
    degraded_pct: float
    unknown_pct: float
    outcome_events: int


class DisplayTotals(BaseModel):
    fresh: float
    fallback: float
    no_snapshot: float
    failed_responses: float
    snapshot_age_seconds: float | None
    uptime: DisplayUptime


class RouteSeries(BaseModel):
    stations: list[float]
    display_data: list[float]


class LatencySeries(BaseModel):
    stations: list[float | None]
    display_data: list[float | None]
    vbb_fetch: list[float | None]


class ChartsSummary(BaseModel):
    bucket_minutes: int
    labels: list[str]
    requests_by_route: RouteSeries
    latency_p50_ms: LatencySeries
    vbb_errors: list[float]
    cache_hit_rate_pct: list[float | None]


class SpyglassStatus(BaseModel):
    reachable: bool
    status: dict | None = None
    error: str | None = None


class ObservabilitySummary(BaseModel):
    generated_at: str
    window: WindowInfo
    display_status: DisplayStatus
    spyglass: SpyglassStatus
    charts: ChartsSummary
    display: DisplayTotals
    vbb: VbbSummary
    log_histogram: LogHistogram
    logs: list[PreparedLogEntry]
