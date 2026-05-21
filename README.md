# Trainspotter

[![CI](https://github.com/momonala/trainspotter/actions/workflows/ci.yml/badge.svg)](https://github.com/momonala/trainspotter/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/momonala/trainspotter/branch/main/graph/badge.svg)](https://codecov.io/gh/momonala/trainspotter)

Flask web app that shows departure boards for nearby Berlin VBB stops, color-coded by whether you have time to walk there. Optionally renders a 1-bit PNG for an ESP32 e-ink display showing departures at a fixed station.

---

## Module layout

```
trainspotter/
├── src/
│   ├── app.py                  # Flask server, endpoint handlers, threading
│   ├── vbb_api.py              # Station snapshot loading, haversine ranking, VBB departures client (LRU cache)
│   ├── utils.py                # Walk time lookup, threshold calc, direction/provenance cleansing, Google Maps cache
│   ├── datamodels.py           # Dataclasses: Station, Departure, Line, Location, Products, Color, Operator
│   ├── image_generator.py      # ESP32: filter departures by quadrant, render 400×300 1-bit PNG
│   ├── departures_fallback.py  # In-memory snapshot fallback for ESP32 when VBB is unreachable
│   ├── config.py               # Typed config accessors (reads pyproject.toml + config.json); exposes FLASK_PORT
│   ├── trainspotter.py         # CLI terminal view (standalone, no server)
│   └── values.example.py       # Template for values.py (git-ignored); set GMAPS_API_KEY here
├── data/
│   └── vbb_stations.json       # Static stop snapshot (~thousands of stops); regenerate with scripts/fetch_stations.py
├── scripts/
│   ├── fetch_stations.py       # Builds vbb_stations.json via VBB /locations/nearby grid sweep
│   └── test_image_render.py    # Dev script to preview ESP32 image output
├── static/
│   ├── app.js                  # Frontend: geolocation, polling, departure rendering, filter controls
│   └── styles.css              # BVG/VBB line colours
├── templates/
│   └── index.html              # Main page template
├── trainspotter_eink/          # ESP32 firmware (Arduino)
│   ├── trainspotter_eink.ino
│   ├── config.h                # WiFi credentials, station ID, server URL (copy from config.example.h)
│   └── README.md               # Hardware wiring, build, and upload instructions
├── install/
│   ├── install.sh              # Raspberry Pi deployment: uv install + systemd + optional Cloudflare tunnel
│   └── projects_trainspotter.service  # systemd unit file
└── config.json                 # User configuration (see Configuration reference below)
```

---

## Architecture

Stops come from a **bundled JSON snapshot** (`data/vbb_stations.json`), not from live VBB location queries on each request. At runtime, the server ranks them by haversine distance, filters by `max_nearby_straightline_m`, then calls VBB only for departures.

### Build-time: stop snapshot

Run when the fetch grid changes or stop metadata needs refreshing (new platforms, renames):

```bash
python scripts/fetch_stations.py
```

```mermaid
flowchart LR
  subgraph buildSnapshot [Build snapshot]
    Script[fetch_stations.py]
    VBBnear[VBB GET locations nearby]
    JsonFile[(data/vbb_stations.json)]
    Script -->|grid of lat/lon anchors| VBBnear
    VBBnear -->|stop metadata| Script
    Script -->|write| JsonFile
  end
```

### Runtime: component relationships

```mermaid
flowchart TB
  subgraph disk [On disk]
    Config[config.json]
    Snapshot[(vbb_stations.json)]
  end

  subgraph server [Flask server]
    Api[app.py endpoints]
    VbbMod[vbb_api.py]
    Utils[utils.py]
    Fallback[departures_fallback.py]
  end

  subgraph external [Network]
    VBBdep[VBB GET stops/id/departures]
    GMaps[Google Directions]
  end

  subgraph client [Browser]
    Js[app.js]
    Page[index.html]
  end

  Config --> Api
  Config --> Utils
  Snapshot -->|loaded at import| VbbMod
  Js -->|POST coords| Api
  Js -->|GET stations| Api
  Api --> VbbMod
  Api --> Utils
  VbbMod -->|LRU cached per station/30s window| VBBdep
  VbbMod -->|on VBBAPIError| Fallback
  Utils -->|walk time if not in config| GMaps
  Api -->|JSON| Js
  Js --> Page
```

---

## Data flow

### Dashboard request flow

1. Browser POSTs coordinates to `/api/location` on geolocation.
2. First `GET /api/stations?refresh=true` resolves nearby stops from the snapshot and caches them server-side.
3. Each stop's departures are fetched in parallel (one thread per stop via `ThreadPoolExecutor`). VBB departures are LRU-cached keyed on `station_id + 30-second timestamp bucket`.
4. Walk time comes from `config.json["stations"]` if the station name matches; otherwise Google Maps (joblib disk cache in `.cache/`).
5. Subsequent polls reuse the cached stop list, re-fetching only departures.

```mermaid
sequenceDiagram
  participant Browser
  participant Flask as FlaskApi
  participant Snapshot as vbb_stations.json
  participant VBB as VBB departures
  participant GMaps as Google Directions

  Browser->>Flask: POST /api/location {lat, lon}
  Browser->>Flask: GET /api/stations?refresh=true
  Flask->>Snapshot: haversine rank within max_nearby_straightline_m
  loop Each selected stop (up to max_dashboard_stations)
    Flask->>GMaps: walking duration (if stop not in config.stations)
    Flask->>VBB: GET /stops/{id}/departures
  end
  Flask-->>Browser: {stations, config}

  loop Every 30s or toolbar refresh
    Browser->>Flask: GET /api/stations
    Note over Flask: Reuses cached stop list
    loop Each pinned stop
      Flask->>VBB: GET /stops/{id}/departures
    end
    Flask-->>Browser: updated departures
  end
```

### ESP32 image flow

`GET /api/esp32/image` bypasses the nearby-stop logic entirely. It uses the fixed `station_id` from `config.json["esp32-display"]`, fetches departures (with in-memory fallback from `departures_fallback.py` on `VBBAPIError`), groups them into four quadrants, and renders a 400×300 1-bit PNG.

---

## Setup & run

**Prerequisites:** Python 3.12+, [uv](https://astral.sh/uv), Google Maps API key with Directions API enabled.

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync
```

Copy and fill in secrets:

```bash
cp src/values.example.py src/values.py
# Set GMAPS_API_KEY in src/values.py
```

Start the server:

```bash
python src/app.py
# http://localhost:5007
```

Terminal-only view (no server):

```bash
python src/trainspotter.py
```

Flask port is set in `pyproject.toml` under `[tool.config]`.

---

## Configuration reference

`config.json` — user-facing runtime configuration.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `stations.<name>.walk_time` | No | int (minutes) | Hardcoded walk time; `<name>` is a substring matched against the station's display name (lowercase). Overrides Google Maps. |
| `walk_time_buffer` | Yes | int (minutes) | Half-width of the yellow zone around walk time. |
| `location.latitude` / `.longitude` | Yes | float | Fallback coordinates used when no browser geolocation is available. |
| `max_nearby_straightline_m` | No | int (meters) | Radius filter for stop selection from snapshot. Default: `1500`. |
| `max_dashboard_stations` | No | int | Caps the number of stops shown on the dashboard. No limit if absent. |
| `update_interval_min` | Yes | int (minutes) | VBB `duration` query param — fetch departures within this window. |
| `min_departure_time_min` | Yes | int (minutes) | Hide departures with fewer than this many minutes remaining. |
| `esp32-display.station_id` | Yes (e-ink) | str | VBB stop ID used by `GET /api/esp32/image`. |
| `esp32-display.station_name` | Yes (e-ink) | str | Display name rendered on the e-ink image header. |
| `esp32-display.quadrants` | Yes (e-ink) | list[4] | Exactly 4 entries. Each: `key` (str), `label` (str), `lines` (list[str]), `direction` (arrow symbol). Order: top-left, top-right, bottom-left, bottom-right. |

**Direction symbols** for quadrant `direction`: `↑ ↓ ← → ↻ ↺` (↻/↺ map to S41/S42 ring direction logic in `utils.get_direction`).

---

## API reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main dashboard page |
| `/api/location` | POST | Set server-side browser coordinates `{latitude, longitude}` |
| `/api/stations` | GET | Nearby stops with live departures. `?refresh=true` re-resolves stop list. |
| `/api/esp32/image` | GET | 400×300 1-bit PNG. `?station_id=<VBB_ID>` overrides config. Returns 502 if VBB fails and no fallback exists. |

### `GET /api/stations` response shape

```json
{
  "stations": [
    {
      "name": "S Gesundbrunnen",
      "distance": 450,
      "walkTime": 15,
      "departures": [
        {
          "transport_type": "S-Bahn",
          "line": "S41",
          "when": "2025-01-01T12:30:00+01:00",
          "direction_symbol": "↻",
          "provenance": "Ringbahn",
          "wait_time": 8
        }
      ],
      "timeConfig": {
        "buffer": 13,
        "yellowThreshold": 17
      }
    }
  ],
  "config": { ... }
}
```

`wait_time = minutes_until_departure − walk_time`. Negative means the train can't be caught.

### Color threshold logic

`timeConfig.buffer` and `timeConfig.yellowThreshold` are derived from `walk_time ± walk_time_buffer`.

For `walk_time=15`, `walk_time_buffer=2`:

| Colour | Condition |
|--------|-----------|
| Red | `wait_time < 13` |
| Yellow | `13 ≤ wait_time ≤ 17` |
| Green | `wait_time > 17` |

### `transport_type` normalisation

VBB `product` → displayed type:

| VBB product | `transport_type` |
|-------------|------------------|
| `suburban` | `S-Bahn` |
| `subway` | `U-Bahn` |
| `tram` | `Tram` |
| `bus` | `Bus` |
| `regional` / `express` | `DB` |

---

## Data models

```
Station
├── id: str                    # VBB stop ID
├── name: str
├── location: Location         # lat/lon
├── products: Products         # suburban, subway, tram, bus, ferry, express, regional (bool each)
├── stationDHID: str
└── distance: int              # meters from user (set at query time)

Departure
├── tripId: str
├── stop: Station
├── when: datetime             # actual departure (delay applied)
├── plannedWhen: datetime
├── delay: int | None          # seconds
├── platform: str | None
├── line: Line
│   ├── name: str              # e.g. "S41"
│   ├── product: str           # e.g. "suburban"
│   └── color: Color | None    # fg, bg hex strings
├── destination: Station | None
└── provenance: str            # raw destination name from VBB (cleansed in utils.cleanse_provenance)
```

---

## External dependencies

### VBB Transport REST API
- Base: `https://v6.vbb.transport.rest`
- No auth required; unofficial API, no SLA.
- `/locations/nearby` — build-time only (`scripts/fetch_stations.py`)
- `/stops/{id}/departures` — live departures; retried up to 3× on 5xx, 5 s timeout per attempt
- Regenerate the local stop list: `python scripts/fetch_stations.py`

### Google Maps Directions API
- Walking mode only; used when a station has no `walk_time` in `config.json`.
- Results are joblib disk-cached in `.cache/` (keyed by origin + destination coordinates).
- Requires `GMAPS_API_KEY` in `src/values.py`.

---

## E-ink display (ESP32)

An ESP32 + 4.2" e-ink (WeAct GDEY042T81, 400×300) fetches `GET /api/esp32/image`, renders the PNG, and deep-sleeps between updates.

Configure in `config.json["esp32-display"]`:
- `station_id`: VBB stop ID
- `station_name`: header text on the image
- `quadrants`: 4 entries specifying which lines and directions fill each quadrant

If the VBB API is unreachable, `departures_fallback.py` shifts the last successful departure times forward by the elapsed age and returns whichever are still in the future.

Firmware, wiring, and upload instructions: [`trainspotter_eink/README.md`](trainspotter_eink/README.md).

---

## Deployment

### systemd (Raspberry Pi)

```bash
./install/install.sh
```

Installs uv, syncs dependencies, installs the systemd service, optionally configures a Cloudflare tunnel.

```bash
sudo systemctl status projects_trainspotter
sudo systemctl restart projects_trainspotter
journalctl -u projects_trainspotter -f
```

Service file: `install/projects_trainspotter.service`.

---

## License

MIT
