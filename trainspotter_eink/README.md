# ESP32 E-Ink Train Display

ESP32 e-ink display showing S-Bahn departures. Fetches pre-rendered PNG image from the Python server and displays it. Deep sleeps between updates to conserve power.

## Hardware

- ESP32 DevKit (any variant)
- WeAct 4.2" e-ink display (400x300, GDEY042T81)

## Pin Connections

| Display Pin | ESP32 Pin | Function |
|------------|-----------|----------|
| VCC        | 3.3V      | Power |
| GND        | GND       | Ground |
| SDA        | GPIO 23   | SPI MOSI (data) |
| SCL        | GPIO 18   | SPI Clock |
| CS         | GPIO 5    | Chip Select |
| D/C        | GPIO 17   | Data/Command |
| Res        | GPIO 16   | Reset |
| BUSY       | GPIO 4    | Busy signal |

## Setup

### 1. Install Dependencies

**Arduino IDE:**
- File → Preferences → Add board URL: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
- Tools → Board → Boards Manager → Install "ESP32"
- Sketch → Include Library → Manage Libraries → Install:
  - **GxEPD2** (by Jean-Marc Zingg)
  - **PNGdec** (by Larry Bank / bitbank2)

**Arduino CLI:**
```bash
arduino-cli core install esp32:esp32
arduino-cli lib install "GxEPD2"
arduino-cli lib install "PNGdec"
```

**Note:** If you get `PNGdec.h: No such file or directory`, make sure the library is installed via Library Manager. The library name in the manager is "PNGdec" by bitbank2.

### 2. Configure

Copy `config.example.h` to `config.h` and edit:

```cpp
#define WIFI_SSID "your_wifi_name"
#define WIFI_PASSWORD "your_wifi_password"
#define API_SERVER_URL "http://your_server_ip:5007"
#define STATION_ID "900110011"  // VBB station ID
#define STATION_NAME "Bornholmer Str."
```

### 3. Start Python Server

```bash
cd trainspotter
uv run python -m src.app
```

### 4. Build & Upload

**Arduino IDE:**
- Tools → Board → ESP32 Dev Module
- Tools → Port → [Select USB port]
- Click Upload

**Arduino CLI:**
```bash
cd trainspotter_eink

# Compile
arduino-cli compile --fqbn esp32:esp32:esp32 .

# Upload (adjust port as needed)
arduino-cli upload --fqbn esp32:esp32:esp32 --port /dev/cu.usbserial-* .

# Monitor serial output
arduino-cli monitor --port /dev/cu.usbserial-* --config baudrate=115200
```

**All-in-one:**
```bash
arduino-cli compile --fqbn esp32:esp32:esp32 . && \
arduino-cli upload --fqbn esp32:esp32:esp32 --port /dev/cu.usbserial-* . && \
arduino-cli monitor --port /dev/cu.usbserial-* --config baudrate=115200
```

## Dataflow:

1. ESP32 wakes from deep sleep
2. Connects to WiFi
3. Fetches PNG image from `GET /api/esp32/image?station_id=XXX`
4. Decodes and renders PNG to e-ink display
5. Enters deep sleep for configured interval

The Python server generates a 400x300 1-bit PNG with:
- Station name and last update time
- 4 quadrants for train groups (S1/2/25/26 up/down, S8/85 up/clockwise)
- Next 2 departures per quadrant (>5 min away)
