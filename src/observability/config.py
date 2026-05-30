"""Trainspotter-specific metric selectors and display state rules.

These are passed to view.py's assembly functions; nothing here imports from
spyglass.server or spyglass.db — only config types from spyglass.dashboard.
"""

# Ordered longest-first so _metric_suffix() disambiguation works correctly.
METRIC_SUFFIXES: tuple[str, ...] = (
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

DISPLAY_STATES: list[str] = ["fresh", "stale", "degraded", "unknown"]

DISPLAY_STATUS_LOOKBACK_MINUTES: int = 5
