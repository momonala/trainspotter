/**
 * Trainspotter ESP32 E-ink Display
 * 
 * Fetches pre-rendered departure image from server and displays on e-ink.
 * Deep sleeps between updates to conserve power.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold9pt7b.h>
#include <esp_sleep.h>
#include <time.h>
#include <PNGdec.h>
#include "config.h"

// NTP
static const char* NTP_SERVER = "pool.ntp.org";
static const long GMT_OFFSET_SEC = 3600;
static const int DST_OFFSET_SEC = 3600;

// Display: WeAct 4.2" e-ink (400x300)
GxEPD2_BW<GxEPD2_420_GDEY042T81, GxEPD2_420_GDEY042T81::HEIGHT> display(
    GxEPD2_420_GDEY042T81(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

PNG png;

// PNG buffer (server sends ~2-4KB for 1-bit 400x300)
const int PNG_BUFFER_SIZE = 16384;
uint8_t* pngBuffer = nullptr;
int pngBufferLen = 0;

// Last successful display update (persists across deep sleep for error screen)
RTC_DATA_ATTR static int lastUpdateDay = -1;
RTC_DATA_ATTR static int lastUpdateMonth = -1;
RTC_DATA_ATTR static int lastUpdateYear = -1;
RTC_DATA_ATTR static int lastUpdateHour = -1;
RTC_DATA_ATTR static int lastUpdateMin = -1;

static const char* const MONTH_ABBREV[] = {
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
};

// PNG decode callback - renders each line to display
// Must return 1 to continue, 0 to abort
int pngDrawLine(PNGDRAW* pDraw) {
    uint16_t lineBuffer[DISPLAY_WIDTH];
    
    // Get line as RGB565 (handles all PNG color types)
    png.getLineAsRGB565(pDraw, lineBuffer, PNG_RGB565_BIG_ENDIAN, 0xFFFF);
    
    // For 1-bit source: white=0xFFFF, black=0x0000
    // Threshold at midpoint for robustness
    for (int x = 0; x < pDraw->iWidth; x++) {
        display.drawPixel(x, pDraw->y, lineBuffer[x] < 0x8000 ? GxEPD_BLACK : GxEPD_WHITE);
    }
    
    return 1;  // Continue decoding
}

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.printf("\n[Trainspotter] %s\n", STATION_NAME);

    display.init(115200);
    display.setRotation(0);
    display.setTextColor(GxEPD_BLACK);

    if (connectWiFi()) {
        if (syncTime()) {
            fetchAndDisplay();
        } else {
            Serial.println("[NTP] Sync failed - showing error with unsynced time");
            showError("Time sync failed", "Will retry after sleep");
        }
        WiFi.disconnect(true);
        WiFi.mode(WIFI_OFF);
    } else {
        showError("WiFi failed", "Check credentials");
    }

    Serial.printf("[Sleep] %d seconds\n", UPDATE_INTERVAL_SECONDS);
    esp_sleep_enable_timer_wakeup((uint64_t)UPDATE_INTERVAL_SECONDS * 1000000ULL);
    esp_deep_sleep_start();
}

void loop() {}

bool connectWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    
    for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) {
        delay(500);
        Serial.print(".");
    }
    Serial.println();
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WiFi] Connected: %s\n", WiFi.localIP().toString().c_str());
        return true;
    }
    
    Serial.printf("[WiFi] Failed (status=%d)\n", WiFi.status());
    return false;
}

bool syncTime() {
    configTime(GMT_OFFSET_SEC, DST_OFFSET_SEC, NTP_SERVER);
    for (int i = 0; i < 10 && time(nullptr) < 1000000000; i++) {
        delay(500);
    }
    struct tm timeinfo;
    if (getLocalTime(&timeinfo)) {
        Serial.println("[NTP] OK");
        return true;
    }
    Serial.println("[NTP] Failed");
    return false;
}

bool fetchAndDisplay() {
    HTTPClient http;
    char url[256];
    snprintf(url, sizeof(url), "%s%s?station_id=%s", API_SERVER_URL, API_ENDPOINT, STATION_ID);
    
    Serial.printf("[HTTP] GET %s\n", url);
    http.begin(url);
    http.setTimeout(API_TIMEOUT_MS);
    
    int httpCode = http.GET();
    if (httpCode != HTTP_CODE_OK) {
        Serial.printf("[HTTP] Error: %d\n", httpCode);
        char msg[32];
        snprintf(msg, sizeof(msg), "HTTP %d", httpCode);
        showError("Server error", msg);
        http.end();
        return false;
    }

    int contentLen = http.getSize();
    if (contentLen <= 0 || contentLen > PNG_BUFFER_SIZE) {
        Serial.printf("[HTTP] Bad size: %d\n", contentLen);
        showError("Download error", "Invalid size");
        http.end();
        return false;
    }

    // Allocate buffer once
    if (!pngBuffer) {
        pngBuffer = (uint8_t*)malloc(PNG_BUFFER_SIZE);
        if (!pngBuffer) {
            Serial.println("[Memory] Allocation failed");
            showError("Memory error", "Out of RAM");
            http.end();
            return false;
        }
    }

    // Download PNG
    WiFiClient* stream = http.getStreamPtr();
    pngBufferLen = 0;
    while (http.connected() && pngBufferLen < contentLen) {
        size_t avail = stream->available();
        if (avail) {
            int n = stream->readBytes(pngBuffer + pngBufferLen, 
                                      min(avail, (size_t)(PNG_BUFFER_SIZE - pngBufferLen)));
            pngBufferLen += n;
        }
        delay(1);
    }
    http.end();

    if (pngBufferLen != contentLen) {
        Serial.printf("[HTTP] Incomplete: %d/%d\n", pngBufferLen, contentLen);
        showError("Download error", "Incomplete");
        return false;
    }

    Serial.printf("[PNG] Downloaded %d bytes\n", pngBufferLen);

    // Decode and render
    display.fillScreen(GxEPD_WHITE);
    int rc = png.openRAM(pngBuffer, pngBufferLen, pngDrawLine);
    
    if (rc != PNG_SUCCESS) {
        Serial.printf("[PNG] Open failed: %d\n", rc);
        showError("Image error", "Decode failed");
        return false;
    }

    Serial.printf("[PNG] %dx%d, %d bpp\n", png.getWidth(), png.getHeight(), png.getBpp());
    
    if (png.getWidth() > DISPLAY_WIDTH || png.getHeight() > DISPLAY_HEIGHT) {
        Serial.println("[PNG] Image too large");
        png.close();
        showError("Image error", "Too large");
        return false;
    }

    png.decode(nullptr, 0);
    png.close();
    display.display(false);  // Full update

    struct tm timeinfo;
    if (getLocalTime(&timeinfo)) {
        lastUpdateDay = timeinfo.tm_mday;
        lastUpdateMonth = timeinfo.tm_mon + 1;
        lastUpdateYear = timeinfo.tm_year + 1900;
        lastUpdateHour = timeinfo.tm_hour;
        lastUpdateMin = timeinfo.tm_min;
    }

    Serial.println("[OK] Display updated");
    return true;
}

void showError(const char* title, const char* detail) {
    display.fillScreen(GxEPD_WHITE);
    display.setFont(&FreeMonoBold9pt7b);
    
    const int margin = 10;
    const int lineH = 20;
    int y = 25;

    display.setCursor(margin, y);
    display.print("ERROR");
    y += lineH;

    display.drawLine(margin, y, DISPLAY_WIDTH - margin, y, GxEPD_BLACK);
    y += 15;

    display.setCursor(margin, y);
    display.print(title);
    y += lineH;

    display.setCursor(margin, y);
    display.print(detail);
    y += lineH + 10;

    display.drawLine(margin, y, DISPLAY_WIDTH - margin, y, GxEPD_BLACK);
    y += 15;

    // Current time (from NTP if synced)
    struct tm timeinfo;
    if (getLocalTime(&timeinfo)) {
        char timeStr[32];
        const char* mon = (timeinfo.tm_mon >= 0 && timeinfo.tm_mon <= 11)
            ? MONTH_ABBREV[timeinfo.tm_mon] : "???";
        snprintf(timeStr, sizeof(timeStr), "Now: %02d %s %02d:%02d",
                 timeinfo.tm_mday, mon, timeinfo.tm_hour, timeinfo.tm_min);
        display.setCursor(margin, y);
        display.print(timeStr);
        y += lineH;
    } else {
        display.setCursor(margin, y);
        display.print("Now: (time not synced)");
        y += lineH;
    }

    // Last successful update
    if (lastUpdateDay >= 0 && lastUpdateMonth >= 1 && lastUpdateMonth <= 12) {
        char lastStr[32];
        snprintf(lastStr, sizeof(lastStr), "Last OK: %02d %s %02d:%02d",
                 lastUpdateDay, MONTH_ABBREV[lastUpdateMonth - 1],
                 lastUpdateHour, lastUpdateMin);
        display.setCursor(margin, y);
        display.print(lastStr);
        y += lineH;
    } else {
        display.setCursor(margin, y);
        display.print("Last OK: never");
        y += lineH;
    }

    display.drawLine(margin, y, DISPLAY_WIDTH - margin, y, GxEPD_BLACK);
    y += 15;

    char info[64];
    snprintf(info, sizeof(info), "Station: %s", STATION_NAME);
    display.setCursor(margin, y);
    display.print(info);
    y += lineH;

    snprintf(info, sizeof(info), "Server: %s", API_SERVER_URL);
    display.setCursor(margin, y);
    display.print(info);

    display.display(false);
}
