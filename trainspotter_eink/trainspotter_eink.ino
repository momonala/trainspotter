/** Trainspotter ESP32: fetch departure image from server, show on e-ink, continuous updates.
 *  config.h must define: WIFI_SSID, WIFI_PASSWORD, API_SERVER_URL, API_ENDPOINT,
 *  STATION_ID, STATION_NAME, API_TIMEOUT_MS, UPDATE_INTERVAL_SECONDS,
 *  EPD_CS, EPD_DC, EPD_RST, EPD_BUSY, DISPLAY_WIDTH, DISPLAY_HEIGHT. */

#include <WiFi.h>
#include <HTTPClient.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeMonoBold9pt7b.h>
#include <time.h>
#include <string.h>
#include <PNGdec.h>
#include "config.h"

// NTP — Europe/Berlin (CET/CEST)
static const char* NTP_SERVER = "pool.ntp.org";
static const long GMT_OFFSET_SEC = 3600;
static const int DST_OFFSET_SEC = 3600;

// WiFi / reconnect / timing
static const unsigned int WIFI_RECONNECT_ATTEMPTS = 10;
static const unsigned int WIFI_RECONNECT_DELAY_MS = 500;
static const unsigned int WIFI_CONNECT_ATTEMPTS = 20;
static const unsigned int WIFI_CONNECT_DELAY_MS = 500;
static const unsigned int NTP_WAIT_ATTEMPTS = 10;
static const unsigned int NTP_WAIT_DELAY_MS = 500;
// Full refresh every REFRESH_COUNT_MAX partial updates to prevent ghosting
static const unsigned int REFRESH_COUNT_MAX = 30;
static const unsigned int BUTTON_POLL_SEC = 1;
static const unsigned int ERROR_RETRY_INTERVAL_SECONDS = 15;
static const unsigned long HTTP_BODY_READ_TIMEOUT_MS = 30000;
static const unsigned int NETWORK_STACK_SETTLE_MS = 800;

// Splash screen layout
static const int SPLASH_MARGIN = 20;
static const int SPLASH_TITLE_OFFSET_Y = 30;
static const int SPLASH_LINE_SPACING = 24;

// Error screen layout
static const int ERROR_MARGIN = 10;
static const int ERROR_LINE_HEIGHT = 20;
static const int ERROR_COUNT_ROW_HEIGHT = 18;
static const int ERROR_DATE_COLUMN_OFFSET = 145;
static const int ERROR_COUNTER_TOP_Y = 10;
static const int ERROR_AFTER_TITLE_SPACING = 8;
static const int ERROR_DIVIDER_SPACING = 18;
static const int ERROR_NUMBER_COLUMN_OFFSET = 290;
static const int ERROR_NUM_BUF_SIZE = 12;
static const int TIMESTAMP_STRING_SIZE = 32;
static const int HTTP_DETAIL_BUFFER_SIZE = 24;

// HTTP / PNG
// NTP epoch threshold: Jan 1, 2001 (validates time sync succeeded)
static const unsigned long NTP_VALID_EPOCH = 1000000000;
// RGB565 white value (all bits set)
static const uint16_t PNG_RGB565_WHITE = 0xFFFF;
// RGB565 threshold: 50% brightness (0x8000 = 32768 = midpoint of 16-bit)
// Pixels below this are rendered as black on e-ink
static const uint16_t PNG_RGB565_BLACK_THRESHOLD = 0x8000;

GxEPD2_BW<GxEPD2_420_GDEY042T81, GxEPD2_420_GDEY042T81::HEIGHT> display(
    GxEPD2_420_GDEY042T81(EPD_CS, EPD_DC, EPD_RST, EPD_BUSY));

PNG png;

const int PNG_BUFFER_SIZE = 16384;
uint8_t* pngBuffer = nullptr;
int pngBufferLen = 0;

static const char* const MONTH_ABBREV[] = {
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
};

enum ErrorType {
    ERR_WIFI,
    ERR_SERVER_CONN_REFUSED,
    ERR_SERVER_CONN_LOST,
    ERR_SERVER_502,
    ERR_SERVER_404,
    ERR_SERVER_OTHER,
    ERR_SERVER_MEMORY,
    ERR_NTP,
    ERR_DOWNLOAD,
    ERR_IMAGE_DECODE,
    ERR_COUNT
};

// Application state - encapsulates all runtime state for clarity and maintainability
struct AppState {
    bool splashShown;
    int refreshCount;
    unsigned long successCycleCount;
    struct tm lastUpdateTime;
    bool lastUpdateValid;
    bool wasShowingError;
    bool showingLiveView;
    bool lastCycleWasSuccess;
    struct tm activeSinceTime;
    bool activeSinceSet;
    ErrorType lastErrorType;
    char lastErrorDetail[HTTP_DETAIL_BUFFER_SIZE];
    unsigned int errorCounts[ERR_COUNT];
};

static AppState appState;

enum HttpStreamResult { HTTP_STREAM_OK, HTTP_STREAM_TIMEOUT, HTTP_STREAM_INCOMPLETE };

static const char* const ERROR_LABELS[ERR_COUNT] = {
    "WiFi connection error",
    "NTP time sync error",
    "HTTP 502 - VBB API error",
    "Server connection lost",
    "Server connection refused",
    "HTTP 404 - not found",
    "other HTTP error",
    "Out of memory",
    "Download error",
    "Image decode error"
};

enum WaitResult {
    WAIT_COMPLETE,
    WAIT_BUTTON_PRESSED,
    WAIT_WIFI_DISCONNECTED
};

void showError(ErrorType errorType, const char* detail = nullptr);
void drawErrorScreen(ErrorType errorType, const char* detail, bool isDebugView);
void handleViewToggle();
bool buttonPressed();
WaitResult waitWithButtonPolling(int totalSeconds, bool checkWiFi);
bool ensureWiFiConnected();
void handleLiveViewCycle();
void handleDebugViewCycle();

void clearScreenAndSetTextStyle() {
    display.fillScreen(GxEPD_WHITE);
    display.setFont(&FreeMonoBold9pt7b);
    display.setTextColor(GxEPD_BLACK);
}

int pngDrawLine(PNGDRAW* drawInfo) {
    uint16_t lineBuffer[DISPLAY_WIDTH];
    png.getLineAsRGB565(drawInfo, lineBuffer, PNG_RGB565_BIG_ENDIAN, PNG_RGB565_WHITE);
    for (int x = 0; x < drawInfo->iWidth; x++) {
        display.drawPixel(x, drawInfo->y, lineBuffer[x] < PNG_RGB565_BLACK_THRESHOLD ? GxEPD_BLACK : GxEPD_WHITE);
    }
    return 1;
}

void showSplash() {
    clearScreenAndSetTextStyle();
    const int margin = SPLASH_MARGIN;
    int y = DISPLAY_HEIGHT / 2 - SPLASH_TITLE_OFFSET_Y;

    display.setCursor(margin, y);
    display.print("Trainspotter");
    y += SPLASH_LINE_SPACING;
    display.setCursor(margin, y);
    display.print(STATION_NAME);
    y += SPLASH_LINE_SPACING;
    display.setCursor(margin, y);
    display.print("Connecting...");
    display.display(false);
}

void fullScreenRefresh() {
    display.setFullWindow();
    display.fillScreen(GxEPD_WHITE);
    display.display(true);
    display.setPartialWindow(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT);
}

void handleViewToggle() {
    appState.showingLiveView = !appState.showingLiveView;
    if (appState.showingLiveView) {
        fullScreenRefresh();
    } else {
        drawErrorScreen(appState.lastErrorType, appState.lastErrorDetail, true);
        display.setFullWindow();
        display.display(true);
        display.setPartialWindow(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT);
    }
}

bool buttonPressed() {
    if (digitalRead(BUTTON_GPIO) != LOW) return false;
    delay(BUTTON_DEBOUNCE_MS);
    if (digitalRead(BUTTON_GPIO) != LOW) return false;
    Serial.println("[Btn] pressed");
    return true;
}

WaitResult waitWithButtonPolling(int waitDurationSeconds, bool monitorWiFiConnection) {
    int elapsedSeconds = 0;
    while (elapsedSeconds < waitDurationSeconds) {
        if (buttonPressed()) {
            return WAIT_BUTTON_PRESSED;
        }
        if (monitorWiFiConnection && WiFi.status() != WL_CONNECTED) {
            return WAIT_WIFI_DISCONNECTED;
        }
        int delaySeconds = min((int)BUTTON_POLL_SEC, waitDurationSeconds - elapsedSeconds);
        delay(delaySeconds * 1000);
        elapsedSeconds += delaySeconds;
    }
    return WAIT_COMPLETE;
}

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.printf("\n[Trainspotter] %s\n", STATION_NAME);

    // Initialize app state
    appState.splashShown = false;
    appState.refreshCount = 0;
    appState.successCycleCount = 0;
    appState.lastUpdateValid = false;
    appState.wasShowingError = false;
    appState.showingLiveView = true;
    appState.lastCycleWasSuccess = false;
    appState.activeSinceSet = false;
    appState.lastErrorType = ERR_SERVER_OTHER;
    appState.lastErrorDetail[0] = '\0';
    for (int i = 0; i < ERR_COUNT; i++) {
        appState.errorCounts[i] = 0;
    }

    display.init(115200, true);
    display.setRotation(0);
    display.setTextColor(GxEPD_BLACK);
    fullScreenRefresh();
    display.setPartialWindow(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT);

    WiFi.setAutoReconnect(true);
    pinMode(BUTTON_GPIO, INPUT_PULLUP);

    if (!appState.splashShown) {
        showSplash();
        appState.splashShown = true;
    }
    Serial.println("[Setup] Ready. Press BOOT for diagnostics.");
}

bool ensureWiFiConnected() {
    if (WiFi.status() == WL_CONNECTED) {
        return true;
    }
    
    Serial.println("[WiFi] Disconnected, reconnecting...");
    WiFi.reconnect();
    for (int i = 0; i < WIFI_RECONNECT_ATTEMPTS && WiFi.status() != WL_CONNECTED; i++) {
        delay(WIFI_RECONNECT_DELAY_MS);
    }
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WiFi] Reconnected: %s\n", WiFi.localIP().toString().c_str());
        delay(NETWORK_STACK_SETTLE_MS);
        return true;
    }
    
    Serial.println("[WiFi] Reconnect failed, connecting from scratch...");
    if (!connectWiFi()) {
        showError(ERR_WIFI);
        Serial.printf("[Wait] %d seconds until retry\n", ERROR_RETRY_INTERVAL_SECONDS);
        WaitResult waitResult = waitWithButtonPolling(ERROR_RETRY_INTERVAL_SECONDS, false);
        if (waitResult == WAIT_BUTTON_PRESSED) {
            handleViewToggle();
        }
        return false;
    }
    
    return true;
}

void handleLiveViewCycle() {
    if (!ensureWiFiConnected()) {
        return;
    }

    bool success = false;
    if (syncTime()) {
        success = fetchAndDisplay();
    } else {
        Serial.println("[NTP] Sync failed");
        showError(ERR_NTP);
    }

    int waitInterval = success ? UPDATE_INTERVAL_SECONDS : ERROR_RETRY_INTERVAL_SECONDS;
    Serial.printf("[Wait] %d seconds until next update\n", waitInterval);
    WaitResult waitResult = waitWithButtonPolling(waitInterval, true);
    if (waitResult == WAIT_BUTTON_PRESSED) {
        handleViewToggle();
    }
}

void handleDebugViewCycle() {
    WaitResult waitResult = waitWithButtonPolling(UPDATE_INTERVAL_SECONDS, false);
    if (waitResult == WAIT_BUTTON_PRESSED) {
        handleViewToggle();
    }
}

void loop() {
    if (buttonPressed()) {
        handleViewToggle();
        return;
    }
    
    if (appState.showingLiveView) {
        handleLiveViewCycle();
    } else {
        handleDebugViewCycle();
    }
}

bool connectWiFi() {
    Serial.println("[WiFi] Connecting...");
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    for (int i = 0; i < WIFI_CONNECT_ATTEMPTS && WiFi.status() != WL_CONNECTED; i++) {
        delay(WIFI_CONNECT_DELAY_MS);
        Serial.print(".");
    }
    Serial.println();
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WiFi] Connected: %s\n", WiFi.localIP().toString().c_str());
        WiFi.setSleep(false);
        delay(NETWORK_STACK_SETTLE_MS);
        return true;
    }
    
    Serial.printf("[WiFi] Failed (status=%d)\n", WiFi.status());
    return false;
}

bool syncTime() {
    Serial.println("[NTP] Syncing...");
    configTime(GMT_OFFSET_SEC, DST_OFFSET_SEC, NTP_SERVER);
    for (int i = 0; i < NTP_WAIT_ATTEMPTS && time(nullptr) < NTP_VALID_EPOCH; i++) {
        delay(NTP_WAIT_DELAY_MS);
    }
    struct tm timeinfo;
    if (getLocalTime(&timeinfo)) {
        if (!appState.activeSinceSet) {
            appState.activeSinceTime = timeinfo;
            appState.activeSinceSet = true;
        }
        Serial.println("[NTP] OK");
        return true;
    }
    Serial.println("[NTP] Failed");
    return false;
}

static bool interpretHttpError(int httpCode, char* errorDetailOut, size_t detailSize, ErrorType* errorTypeOut) {
    if (httpCode == HTTP_CODE_OK) return false;
    if (httpCode == -1) {
        errorDetailOut[0] = '\0';
        *errorTypeOut = ERR_SERVER_CONN_REFUSED;
    } else if (httpCode == -11) {
        errorDetailOut[0] = '\0';
        *errorTypeOut = ERR_SERVER_CONN_LOST;
    } else if (httpCode == 502) {
        errorDetailOut[0] = '\0';
        *errorTypeOut = ERR_SERVER_502;
    } else if (httpCode == 404) {
        errorDetailOut[0] = '\0';
        *errorTypeOut = ERR_SERVER_404;
    } else {
        snprintf(errorDetailOut, detailSize, "HTTP %d", httpCode);
        *errorTypeOut = ERR_SERVER_OTHER;
    }
    return true;
}

static HttpStreamResult streamBodyToBuffer(HTTPClient* http, uint8_t* buffer, size_t bufferMaxSize, int contentLength,
                                           unsigned long timeoutMs, size_t* bytesReadOut) {
    Stream* stream = http->getStreamPtr();
    *bytesReadOut = 0;
    const unsigned long startTimeMs = millis();
    while (http->connected() && *bytesReadOut < (size_t)contentLength) {
        if (millis() - startTimeMs >= timeoutMs) return HTTP_STREAM_TIMEOUT;
        size_t availableBytes = stream->available();
        if (availableBytes) {
            int bytesRead = stream->readBytes(buffer + *bytesReadOut, min(availableBytes, bufferMaxSize - *bytesReadOut));
            *bytesReadOut += bytesRead;
        }
        delay(1);
    }
    return *bytesReadOut == (size_t)contentLength ? HTTP_STREAM_OK : HTTP_STREAM_INCOMPLETE;
}

/**
 * Draw decoded PNG to display page-by-page.
 * 
 * Note: The e-ink driver's firstPage/nextPage pattern requires reopening
 * the PNG for each page because the decoder state is not preserved between
 * pages. This is a limitation of the PNGdec library with GxEPD2.
 */
bool drawPngToDisplay() {
    display.firstPage();
    do {
        png.close();
        int result = png.openRAM(pngBuffer, pngBufferLen, pngDrawLine);
        if (result != PNG_SUCCESS) {
            Serial.printf("[PNG] Page decode failed %d\n", result);
            png.close();
            showError(ERR_IMAGE_DECODE, "Page decode");
            return false;
        }
        png.decode(nullptr, 0);
    } while (display.nextPage());
    
    png.close();
    return true;
}

static bool setupHttpRequest(HTTPClient* http) {
    char url[256];
    snprintf(url, sizeof(url), "%s%s?station_id=%s", 
             API_SERVER_URL, API_ENDPOINT, STATION_ID);
    Serial.printf("[HTTP] GET %s\n", url);
    http->begin(url);
    http->setTimeout(API_TIMEOUT_MS);
    return true;
}

static bool downloadPngData(HTTPClient* http, size_t* downloadedSizeOut) {
    int contentLength = http->getSize();
    Serial.printf("[HTTP] Content-Length %d\n", contentLength);
    if (contentLength <= 0 || contentLength > PNG_BUFFER_SIZE) {
        Serial.printf("[HTTP] Invalid size %d\n", contentLength);
        showError(ERR_DOWNLOAD, "Size");
        return false;
    }

    if (!pngBuffer) {
        pngBuffer = (uint8_t*)malloc(PNG_BUFFER_SIZE);
        if (!pngBuffer) {
            Serial.println("[Memory] Allocation failed");
            showError(ERR_SERVER_MEMORY, "Out of memory");
            return false;
        }
    }

    size_t bytesRead = 0;
    const unsigned long downloadStartTimeMs = millis();
    HttpStreamResult streamResult = streamBodyToBuffer(http, pngBuffer, PNG_BUFFER_SIZE, contentLength,
                                                    HTTP_BODY_READ_TIMEOUT_MS, &bytesRead);
    Serial.printf("[HTTP] Body %d bytes in %lu ms\n", (int)bytesRead, (unsigned long)(millis() - downloadStartTimeMs));
    
    if (streamResult != HTTP_STREAM_OK) {
        const char* errorDetail = (streamResult == HTTP_STREAM_TIMEOUT) ? "Timeout" : "Incomplete";
        if (streamResult == HTTP_STREAM_TIMEOUT) {
            Serial.println("[HTTP] Body read timeout");
        } else {
            Serial.printf("[HTTP] Incomplete %d/%d\n", (int)bytesRead, contentLength);
        }
        showError(ERR_DOWNLOAD, errorDetail);
        return false;
    }
    
    *downloadedSizeOut = bytesRead;
    return true;
}

static bool validateAndDecodePng(size_t pngSize) {
    pngBufferLen = pngSize;
    
    int decodeResult = png.openRAM(pngBuffer, pngBufferLen, pngDrawLine);
    if (decodeResult != PNG_SUCCESS) {
        Serial.printf("[PNG] Decode failed %d\n", decodeResult);
        showError(ERR_IMAGE_DECODE);
        return false;
    }

    if (png.getWidth() > DISPLAY_WIDTH || png.getHeight() > DISPLAY_HEIGHT) {
        Serial.printf("[PNG] Too large %dx%d\n", png.getWidth(), png.getHeight());
        png.close();
        showError(ERR_IMAGE_DECODE);
        return false;
    }
    
    png.close();
    return true;
}

static void updateDisplayState() {
    struct tm timeinfo;
    if (getLocalTime(&timeinfo)) {
        appState.lastUpdateTime = timeinfo;
        appState.lastUpdateValid = true;
    }

    appState.successCycleCount++;
    appState.lastCycleWasSuccess = true;
    appState.showingLiveView = true;
    appState.wasShowingError = false;
}

bool fetchAndDisplay() {
    HTTPClient http;
    
    if (!setupHttpRequest(&http)) {
        return false;
    }
    
    int httpCode = http.GET();
    Serial.printf("[HTTP] GET returned code: %d\n", httpCode);
    
    char errorDetail[HTTP_DETAIL_BUFFER_SIZE];
    ErrorType errorType;
    if (interpretHttpError(httpCode, errorDetail, sizeof(errorDetail), &errorType)) {
        Serial.printf("[HTTP] Error: %d -> %s\n", httpCode, ERROR_LABELS[errorType]);
        showError(errorType, errorDetail);
        http.end();
        return false;
    }
    Serial.println("[HTTP] 200 OK");
    
    size_t pngSize = 0;
    if (!downloadPngData(&http, &pngSize)) {
        http.end();
        return false;
    }
    http.end();
    
    if (!validateAndDecodePng(pngSize)) {
        return false;
    }

    bool transitioningToLiveView = appState.wasShowingError && !appState.showingLiveView;
    if (transitioningToLiveView) {
        fullScreenRefresh();
    }

    // Check if we need a full refresh for anti-ghosting
    bool needsFullRefresh = (appState.refreshCount >= REFRESH_COUNT_MAX);
    if (needsFullRefresh) {
        display.setFullWindow();
        appState.refreshCount = 0;
    }

    if (!drawPngToDisplay()) {
        // Restore partial window if we switched to full window
        if (needsFullRefresh) {
            display.setPartialWindow(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT);
        }
        return false;
    }

    // Restore partial window after full refresh cycle
    if (needsFullRefresh) {
        display.setPartialWindow(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT);
    } else {
        // Increment refresh count for partial updates
        appState.refreshCount++;
    }

    updateDisplayState();
    Serial.println("[OK] Updated");
    return true;
}

/** Calculate minutes elapsed since the given time. Returns -1 on error. */
static long calculateMinutesAgo(const struct tm* then) {
    struct tm copy = *then;
    time_t thenSec = mktime(&copy);
    time_t nowSec = time(nullptr);
    if (thenSec == (time_t)-1 || nowSec == (time_t)-1) return -1;
    return (long)(nowSec - thenSec) / 60;
}

static void formatAgeString(long minutesElapsed, char* ageStringOut, size_t bufferSize) {
    if (minutesElapsed < 0) minutesElapsed = 0;
    unsigned long days = (unsigned long)(minutesElapsed / (60 * 24));
    unsigned long hours = (unsigned long)((minutesElapsed % (60 * 24)) / 60);
    unsigned long minutes = (unsigned long)(minutesElapsed % 60);
    
    if (days > 0) {
        snprintf(ageStringOut, bufferSize, "%lud%luh%lum", days, hours, minutes);
    } else if (hours > 0) {
        snprintf(ageStringOut, bufferSize, "%luh%lum", hours, minutes);
    } else {
        snprintf(ageStringOut, bufferSize, "%lum", minutes);
    }
}

static void formatTimestampWithMinutesAgo(const struct tm* timeInfo, char* timestampStringOut, size_t bufferSize) {
    int bytesWritten = snprintf(timestampStringOut, bufferSize, "%02d %s %02d:%02d",
                     timeInfo->tm_mday, MONTH_ABBREV[timeInfo->tm_mon], timeInfo->tm_hour, timeInfo->tm_min);
    if (bytesWritten <= 0 || (size_t)bytesWritten >= bufferSize) return;
    
    long minutesAgo = calculateMinutesAgo(timeInfo);
    if (minutesAgo < 0) return;
    
    char ageString[32];
    formatAgeString(minutesAgo, ageString, sizeof(ageString));
    snprintf(timestampStringOut + (size_t)bytesWritten, bufferSize - (size_t)bytesWritten, " (%s)", ageString);
}

static void drawErrorHeader(ErrorType errorType, const char* detail, bool isDebugView, int* y) {
    const int margin = ERROR_MARGIN;
    const int lineHeight = ERROR_LINE_HEIGHT;

    display.setCursor(margin, *y);
    display.print("Successful cycles: ");
    display.print(appState.successCycleCount);
    *y += lineHeight;

    display.setCursor(margin, *y);
    if (isDebugView) {
        display.print("Error debug view");
    } else {
        display.print("ERROR: ");
        display.print(ERROR_LABELS[errorType]);
        if (detail && detail[0] != '\0') {
            display.print(" (");
            display.print(detail);
            display.print(")");
        }
    }
    *y += ERROR_AFTER_TITLE_SPACING;
    display.drawLine(margin, *y, DISPLAY_WIDTH - margin, *y, GxEPD_BLACK);
    *y += ERROR_DIVIDER_SPACING;
}

static void drawErrorTimestamps(int* y) {
    const int margin = ERROR_MARGIN;
    const int lineHeight = ERROR_LINE_HEIGHT;
    const int timestampColumnX = margin + ERROR_DATE_COLUMN_OFFSET;

    display.setCursor(margin, *y);
    display.print("Active since: ");
    if (appState.activeSinceSet && appState.activeSinceTime.tm_mon >= 0 && appState.activeSinceTime.tm_mon <= 11) {
        char activeSinceTimeString[TIMESTAMP_STRING_SIZE];
        formatTimestampWithMinutesAgo(&appState.activeSinceTime, activeSinceTimeString, sizeof(activeSinceTimeString));
        display.setCursor(timestampColumnX, *y);
        display.print(activeSinceTimeString);
    } else {
        display.setCursor(timestampColumnX, *y);
        display.print("(time not synced)");
    }
    *y += lineHeight;

    struct tm currentTimeInfo;
    display.setCursor(margin, *y);
    display.print("Now: ");
    if (getLocalTime(&currentTimeInfo)) {
        char currentTimeString[TIMESTAMP_STRING_SIZE];
        const char* monthAbbreviation = (currentTimeInfo.tm_mon >= 0 && currentTimeInfo.tm_mon <= 11)
            ? MONTH_ABBREV[currentTimeInfo.tm_mon] : "???";
        snprintf(currentTimeString, sizeof(currentTimeString), "%02d %s %02d:%02d",
                 currentTimeInfo.tm_mday, monthAbbreviation, currentTimeInfo.tm_hour, currentTimeInfo.tm_min);
        display.setCursor(timestampColumnX, *y);
        display.print(currentTimeString);
    } else {
        display.setCursor(timestampColumnX, *y);
        display.print("(time not synced)");
    }
    *y += lineHeight;

    display.setCursor(margin, *y);
    display.print("Last OK: ");
    if (appState.lastUpdateValid && appState.lastUpdateTime.tm_mon >= 0 && appState.lastUpdateTime.tm_mon <= 11) {
        char lastUpdateTimeString[TIMESTAMP_STRING_SIZE];
        formatTimestampWithMinutesAgo(&appState.lastUpdateTime, lastUpdateTimeString, sizeof(lastUpdateTimeString));
        display.setCursor(timestampColumnX, *y);
        display.print(lastUpdateTimeString);
    } else {
        display.setCursor(timestampColumnX, *y);
        display.print("never");
    }
    *y += ERROR_AFTER_TITLE_SPACING;
    display.drawLine(margin, *y, DISPLAY_WIDTH - margin, *y, GxEPD_BLACK);
    *y += ERROR_DIVIDER_SPACING;
}

static void drawErrorCountTable(int y) {
    const int margin = ERROR_MARGIN;
    const int numberColumnX = margin + ERROR_NUMBER_COLUMN_OFFSET;
    char countString[ERROR_NUM_BUF_SIZE];
    for (int i = 0; i < ERR_COUNT; i++) {
        display.setCursor(margin, y);
        display.print(ERROR_LABELS[i]);
        snprintf(countString, sizeof(countString), "%u", appState.errorCounts[i]);
        display.setCursor(numberColumnX, y);
        display.print(countString);
        y += ERROR_COUNT_ROW_HEIGHT;
    }
}

void drawErrorScreen(ErrorType errorType, const char* detail, bool isDebugView) {
    clearScreenAndSetTextStyle();
    int y = ERROR_COUNTER_TOP_Y;
    drawErrorHeader(errorType, detail, isDebugView, &y);
    drawErrorTimestamps(&y);
    drawErrorCountTable(y);
    display.display(true);
}

void showError(ErrorType errorType, const char* detail) {
    bool transitioningToError = !appState.wasShowingError;
    if (transitioningToError) {
        fullScreenRefresh();
    }
    
    appState.wasShowingError = true;
    
    if (errorType >= 0 && errorType < ERR_COUNT) {
        appState.lastErrorType = errorType;
        if (detail) {
            strncpy(appState.lastErrorDetail, detail, HTTP_DETAIL_BUFFER_SIZE - 1);
            appState.lastErrorDetail[HTTP_DETAIL_BUFFER_SIZE - 1] = '\0';
        } else {
            appState.lastErrorDetail[0] = '\0';
        }
        appState.errorCounts[errorType]++;
        appState.lastCycleWasSuccess = false;
        if (detail) {
            Serial.printf("[Error] %s (%s)\n", ERROR_LABELS[errorType], detail);
        } else {
            Serial.printf("[Error] %s\n", ERROR_LABELS[errorType]);
        }
    }
    drawErrorScreen(errorType, detail, false);
}
