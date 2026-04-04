"""Download VBB station data and save as a local JSON snapshot.

Uses GET /locations/nearby on a dense grid over Berlin–Brandenburg. The API
returns a small number of stops per call; wide geographic coverage is required
so North/Wedding stops (e.g. Bornholmerstraße) are not missing from the file.

Run after changing grid constants, or when VBB adds or moves stops:
    python scripts/fetch_stations.py
"""

import json
import sys
import time
from pathlib import Path

import requests

API_BASE = "https://v6.vbb.transport.rest"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "vbb_stations.json"

# Bounding box ~greater Berlin; step ~6–7 km so each /nearby call discovers local stops.
MIN_LAT = 52.38
MAX_LAT = 52.68
MIN_LON = 13.05
MAX_LON = 13.65
GRID_STEP = 0.06
# v6.vbb.transport.rest: ~100 req/min — stay under with pause between grid calls.
REQUEST_PAUSE_S = 0.65

RESULTS_PER_POINT = 500
TIMEOUT = 15

_SNAPSHOT_FIELDS = frozenset({"type", "id", "name", "location", "products", "stationDHID"})


def _grid_axis(min_v: float, max_v: float, step: float) -> list[float]:
    """Inclusive axis from min_v to max_v in step increments (both ends on-grid)."""
    n = int(round((max_v - min_v) / step))
    return [round(min_v + i * step, 5) for i in range(n + 1)]


def berlin_grid_anchor_points() -> list[tuple[float, float]]:
    """Lat/lon anchors for staggered /locations/nearby calls."""
    lats = _grid_axis(MIN_LAT, MAX_LAT, GRID_STEP)
    lons = _grid_axis(MIN_LON, MAX_LON, GRID_STEP)
    return [(lat, lon) for lat in lats for lon in lons]


def fetch_nearby(lat: float, lon: float) -> list[dict]:
    """GET /locations/nearby for one anchor point."""
    resp = requests.get(
        f"{API_BASE}/locations/nearby",
        params={"latitude": lat, "longitude": lon, "results": RESULTS_PER_POINT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _trim_stop_for_snapshot(stop: dict) -> dict | None:
    """Return a minimal stop dict for the snapshot, or None if the payload is unusable."""
    stop_id = stop.get("id")
    if not stop_id or "location" not in stop or "products" not in stop:
        return None
    row = {k: stop[k] for k in _SNAPSHOT_FIELDS if k in stop}
    row.setdefault("stationDHID", "")
    return row


def _collect_unique_stops() -> list[dict]:
    """Merge grid fetches; first-seen id wins (order follows `berlin_grid_anchor_points`)."""
    seen_ids: set[str] = set()
    stations: list[dict] = []
    grid = berlin_grid_anchor_points()
    total = len(grid)

    for i, (lat, lon) in enumerate(grid):
        print(f"[{i + 1}/{total}] Fetching near ({lat}, {lon})...")
        try:
            raw = fetch_nearby(lat, lon)
        except requests.RequestException as exc:
            print(f"  ⚠️  Failed: {exc}", file=sys.stderr)
            continue

        added = 0
        for stop in raw:
            trimmed = _trim_stop_for_snapshot(stop)
            if trimmed is None:
                continue
            stop_id = trimmed["id"]
            if stop_id in seen_ids:
                continue
            seen_ids.add(stop_id)
            stations.append(trimmed)
            added += 1
        print(f"  +{added} new (total {len(stations)})")

        if i < total - 1:
            time.sleep(REQUEST_PAUSE_S)

    stations.sort(key=lambda s: s["id"])
    return stations


def main() -> None:
    stations = _collect_unique_stops()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(stations, indent=2, ensure_ascii=False) + "\n")
    print(f"\n✅ Saved {len(stations)} stations → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
