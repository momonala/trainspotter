'use strict';

// =============================================================================
// Config
// =============================================================================

const DISPLAY_CONFIG = {
    FETCH_TIMEOUT_MS:    30000,
    REFRESH_INTERVAL_MS: 30000,
    CLOCK_INTERVAL_MS:   1000,
};

const SCHEDULE_CONFIG = {
    LS_KEY: 'displaySchedules',
    DEFAULT_TOLERANCE_MIN: 4,  // ± window default: [target − N, target + N]
    MAX_TOLERANCE_MIN: 25,     // tolerance wheel runs 0…MAX
};

// Direction arrow → human label for the route picker wheel.
const DIRECTION_LABELS = {
    '↑': 'Up',
    '↓': 'Down',
    '←': 'Left',
    '→': 'Right',
    '↻': 'Clockwise',
    '↺': 'Counter',
};

const FLOOR_MINUTES_OFFSET_MS = 59_000;
const WHEEL_ITEM_PX = 60; // keep in sync with --schedule-wheel-item-height in display.css
const BERLIN_TZ = 'Europe/Berlin';
const OVERLAY_CLOSE_MS = 200;

// Ordered Mon→Sun for display; stored as JS dow (0=Sun, 1=Mon … 6=Sat).
const DAYS_OF_WEEK = [
    { dow: 1, label: 'Mo' },
    { dow: 2, label: 'Tu' },
    { dow: 3, label: 'We' },
    { dow: 4, label: 'Th' },
    { dow: 5, label: 'Fr' },
    { dow: 6, label: 'Sa' },
    { dow: 0, label: 'Su' },
];

// =============================================================================
// State
// =============================================================================

const state = {
    lastData: null,
    lastUpdatedAt: null,
    quadrantsByKey: new Map(),
    lastRenderedSnapshot: null,
    lastAgedElapsedMin: null,
    lastError: null,
};

// Zoom modal state
const zoom = {
    active: false,
    departureTime: null,  // absolute ms — computed when zoom opens
    alarmArmed: false,    // flips false once alarm fires (threshold: 7 min)
    autoCloseArmed: false,// flips false once auto-dismiss fires (threshold: 5 min)
};

let alarmTimerId = null;
let alarmAutoStopId = null;  // clears the alarm sound after 60 s
let audioCtx = null;         // shared context — must be created inside a user gesture
let muted = localStorage.getItem('alarmMuted') === 'true';
let refreshInFlight = false;
const warnedMissingQuadrantKeys = new Set();

// =============================================================================
// API
// =============================================================================

/**
 * Human-readable label for a structured VBB failure in diagnostics.
 * @param {{ vbb_error_summary?: string, vbb_error_kind?: string, vbb_http_status?: number } | null | undefined} diag
 * @returns {{ short: string, badge: string }}
 */
function describeVbbUpstream(diag) {
    const summary = diag?.vbb_error_summary;
    if (summary) {
        return { short: summary, badge: `⚠\u2009${summary}` };
    }
    const httpStatus = diag?.vbb_http_status;
    if (httpStatus != null) {
        const short = `VBB returned ${httpStatus}`;
        return { short, badge: `⚠\u2009${short}` };
    }
    const kind = diag?.vbb_error_kind;
    if (kind === 'timeout') {
        return { short: 'VBB timed out', badge: '⚠\u2009VBB timed out' };
    }
    if (kind === 'connection') {
        return { short: 'VBB connection failed', badge: '⚠\u2009VBB connection failed' };
    }
    return { short: 'VBB unreachable', badge: '⚠\u2009VBB unreachable' };
}

/**
 * Map a failed display fetch to engineering-facing copy.
 * Backend /api/display/data returns HTTP 502 when VBB fails with no fallback,
 * or HTTP 500 for unexpected handler errors. The upstream VBB failure type
 * (timeout, 502, 503, …) is in diagnostics, not the HTTP status code.
 *
 * @returns {{ title: string, subtitle: string, badge: string }}
 */
function describeDisplayFetchError(err) {
    const status = err.httpStatus != null ? Number(err.httpStatus) : null;
    const diag = err.serverDiagnostics ?? null;

    if (err.name === 'AbortError') {
        const timeoutMs = DISPLAY_CONFIG.FETCH_TIMEOUT_MS / 1000;
        return {
            title: `Client timeout (${timeoutMs}s)`,
            subtitle: `GET /api/display/data client timeout · browser gave up after ${timeoutMs}s`,
            badge: '⚠\u2009client timeout',
        };
    }

    if (status === 502) {
        const upstream = describeVbbUpstream(diag);
        const detail = err.serverDetail || 'No cached snapshot with future departures';
        const parts = [upstream.short, detail];
        if (diag?.vbb_error && !parts.some(p => diag.vbb_error.includes(p))) {
            parts.push(diag.vbb_error);
        }
        return {
            title: `No departures · ${upstream.short}`,
            subtitle: parts.join(' · '),
            badge: upstream.badge,
        };
    }

    if (status === 500) {
        const detail = err.serverDetail || err.serverError || 'Unexpected error in /api/display/data';
        return {
            title: '500 · display handler failed',
            subtitle: detail,
            badge: '⚠\u2009500 · server error',
        };
    }

    if (status != null) {
        return {
            title: `HTTP ${status}${err.serverError ? ` · ${err.serverError}` : ''}`,
            subtitle: err.serverDetail || err.message || 'Non-OK response from /api/display/data',
            badge: `⚠\u2009HTTP ${status}`,
        };
    }

    return {
        title: 'Network error · fetch failed',
        subtitle: err.message || 'Browser could not reach GET /api/display/data',
        badge: '⚠\u2009network error',
    };
}

async function fetchDisplayData() {
    const url = '/api/display/data';
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), DISPLAY_CONFIG.FETCH_TIMEOUT_MS);
    try {
        const resp = await fetch(url, { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!resp.ok) {
            let body = {};
            try {
                body = await resp.json();
            } catch {
                /* non-JSON error body (proxy HTML, etc.) */
            }
            const err = new Error(body.error || `HTTP ${resp.status}`);
            err.name = 'DisplayFetchError';
            err.httpStatus = resp.status;
            err.serverError = body.error ?? null;
            err.serverDetail = body.detail ?? null;
            err.serverDiagnostics = body.diagnostics ?? null;
            throw err;
        }
        return await resp.json();
    } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === 'AbortError') {
            console.error('🚆 display fetch timed out', { url, timeoutMs: DISPLAY_CONFIG.FETCH_TIMEOUT_MS });
        } else if (err.httpStatus != null) {
            console.error('🚆 display fetch HTTP error', {
                status: err.httpStatus,
                error: err.serverError,
                detail: err.serverDetail,
            });
        } else {
            console.error(`🚆 display fetch failed: ${err?.message || err}`);
        }
        throw err;
    }
}

// =============================================================================
// Stale-data aging (client-side when polls fail or between refreshes)
// =============================================================================

/** JSON snapshot of quadrant departures — used to skip redundant re-renders. */
function departuresSnapshot(quadrants) {
    return JSON.stringify(
        (quadrants ?? []).map(q =>
            (q.departures ?? []).map(dep => `${dep.line}:${dep.minutes}`),
        ),
    );
}

/**
 * Shift floor-minute departures forward by elapsed time since last fetch and
 * drop trains that are no longer catchable (same min_departure_min gate as server).
 */
function ageDisplayData(data, fetchedAtMs, nowMs = Date.now()) {
    if (!data || fetchedAtMs == null) return data;

    const elapsedMin = Math.floor((nowMs - fetchedAtMs) / 60_000);
    const minMinutes = data.min_departure_min ?? 5;

    const quadrants = (data.quadrants ?? []).map(q => ({
        ...q,
        departures: (q.departures ?? [])
            .map(dep => ({ ...dep, minutes: dep.minutes - elapsedMin }))
            .filter(dep => dep.minutes >= minMinutes),
    }));

    return { ...data, quadrants };
}

function renderAgedQuadrantsIfNeeded(nowMs = Date.now()) {
    if (!state.lastData || state.lastUpdatedAt == null) return;

    const elapsedMin = Math.floor((nowMs - state.lastUpdatedAt) / 60_000);
    if (elapsedMin === state.lastAgedElapsedMin) return;

    const aged = ageDisplayData(state.lastData, state.lastUpdatedAt, nowMs);
    const snapshot = departuresSnapshot(aged.quadrants);
    state.lastAgedElapsedMin = elapsedMin;
    if (snapshot === state.lastRenderedSnapshot) return;

    rebuildQuadrantIndex(aged);
    state.lastRenderedSnapshot = snapshot;
    renderQuadrants(aged);
}

// =============================================================================
// Display serving status (timer colour + header badge)
// =============================================================================

/** @typedef {'loading' | 'fresh' | 'stale' | 'error'} DisplayServingStatus */

/**
 * Derive UI state from data provenance, not poll recency alone.
 * @returns {DisplayServingStatus}
 */
function resolveDisplayStatus() {
    if (state.lastData) {
        if (state.lastData.used_fallback) {
            return 'stale';
        }
        return 'fresh';
    }
    if (state.lastError) {
        return 'error';
    }
    return 'loading';
}

function formatRelativeAgo(fromMs) {
    if (fromMs == null) return '';
    const secs = Math.max(0, Math.floor((Date.now() - fromMs) / 1000));
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    const pad = n => String(n).padStart(2, '0');
    return m > 0 ? `${m}:${pad(s)} ago` : `${s}s ago`;
}

function formatLastUpdatedLabel() {
    if (state.lastUpdatedAt == null) return '';
    return `(last updated ${formatRelativeAgo(state.lastUpdatedAt)})`;
}

/**
 * @param {DisplayServingStatus} status
 * @returns {string | null}
 */
function getStatusBadgeText(status) {
    if (status === 'error') {
        return state.lastError?.badge ?? '⚠\u2009no data';
    }
    if (status !== 'stale') {
        return null;
    }
    // Status is 'stale' only when used_fallback is true
    if (state.lastData?.used_fallback) {
        const upstream = describeVbbUpstream(state.lastData.diagnostics);
        return `⚠\u2009stale snapshot · ${upstream.short}`;
    }
    return null;
}

function updateClock() {
    const el = document.getElementById('header-time');
    if (!el) return;
    el.textContent = new Date().toLocaleTimeString('de-DE', {
        timeZone: BERLIN_TZ,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

/** Sync header timer colour and status badge with resolveDisplayStatus(). */
function updateDisplayStatus() {
    const status = resolveDisplayStatus();
    const timerEl = document.getElementById('last-updated-ago');
    const badgeEl = document.getElementById('stale-badge');

    if (timerEl) {
        timerEl.classList.remove(
            'last-updated-ago--fresh',
            'last-updated-ago--stale',
            'last-updated-ago--error',
            'last-updated-ago--loading',
        );
        if (status === 'loading') {
            timerEl.classList.add('last-updated-ago--loading');
            timerEl.textContent = '';
        } else if (status === 'fresh') {
            timerEl.classList.add('last-updated-ago--fresh');
            timerEl.textContent = formatLastUpdatedLabel();
        } else if (status === 'stale') {
            timerEl.classList.add('last-updated-ago--stale');
            timerEl.textContent = formatLastUpdatedLabel();
        } else {
            timerEl.classList.add('last-updated-ago--error');
            timerEl.textContent = state.lastUpdatedAt
                ? formatLastUpdatedLabel()
                : '(no data)';
        }
    }

    if (badgeEl) {
        const badgeText = getStatusBadgeText(status);
        badgeEl.classList.remove('visible', 'stale-badge--error');
        if (badgeText) {
            badgeEl.classList.add('visible');
            badgeEl.textContent = badgeText;
            if (status === 'error') {
                badgeEl.classList.add('stale-badge--error');
            }
            badgeEl.setAttribute(
                'aria-label',
                status === 'error' ? 'Error — tap for details' : 'Stale data — tap for details',
            );
        } else {
            badgeEl.setAttribute('aria-label', 'Fetch status — tap for details');
        }
    }
}

// =============================================================================
// Alarm
// =============================================================================

/**
 * Ensure we have a running AudioContext.
 * MUST be called from inside a user-gesture handler (tap/click) so iOS
 * Safari allows the context to move out of its initial "suspended" state.
 */
function unlockAudio() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    if (!audioCtx) audioCtx = new AudioCtx();
    if (audioCtx.state === 'suspended') audioCtx.resume();
}

function toggleMute() {
    muted = !muted;
    localStorage.setItem('alarmMuted', muted);
    const btn = document.getElementById('mute-btn');
    if (!btn) return;
    btn.classList.toggle('muted', muted);
    btn.setAttribute('aria-pressed', String(muted));
    btn.setAttribute('aria-label', muted ? 'Unmute alarm' : 'Mute alarm');
    if (muted) stopAlarm();
}

function beepOnce() {
    if (muted || !audioCtx || audioCtx.state !== 'running') return;
    // 6 rapid alternating pulses — harsh square wave, hard to ignore
    [0, 0.15, 0.30, 0.45, 0.60, 0.75].forEach((t, i) => {
        const freq = i % 2 === 0 ? 1320 : 880;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.type = 'square';
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.45, audioCtx.currentTime + t);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + t + 0.12);
        osc.start(audioCtx.currentTime + t);
        osc.stop(audioCtx.currentTime + t + 0.13);
    });
}

function playAlarm() {
    stopAlarm();
    beepOnce();
    alarmTimerId = setInterval(beepOnce, 1800);
    alarmAutoStopId = setTimeout(stopAlarm, 60_000);
}

function stopAlarm() {
    if (alarmTimerId !== null) {
        clearInterval(alarmTimerId);
        alarmTimerId = null;
    }
    if (alarmAutoStopId !== null) {
        clearTimeout(alarmAutoStopId);
        alarmAutoStopId = null;
    }
}

// =============================================================================
// Zoom modal
// =============================================================================

function updateZoomDisplay() {
    if (!zoom.active) return;

    const msLeft = zoom.departureTime - Date.now();
    const minutes = Math.max(0, Math.floor(msLeft / 60_000));

    const minsEl = document.getElementById('modal-minutes');
    const clockEl = document.getElementById('modal-clock');
    if (minsEl) minsEl.textContent = `${minutes}m`;
    if (clockEl) {
        clockEl.textContent = new Date(zoom.departureTime).toLocaleTimeString('de-DE', {
            hour: '2-digit', minute: '2-digit',
        });
    }

    // Fire alarm the first time the countdown reaches ≤ 7 min
    if (zoom.alarmArmed && minutes <= 7) {
        zoom.alarmArmed = false;
        document.getElementById('modal-card')?.classList.add('alarming');
        playAlarm();
    }

    // Auto-dismiss zoom when the train is ≤ 5 min away (time to move)
    if (zoom.autoCloseArmed && minutes <= 5) {
        zoom.autoCloseArmed = false;
        closeZoom();
    }

    syncVbbWarning();
}

function vbbWarningMessage(diag) {
    const upstream = describeVbbUpstream(diag);
    return `⚠ ${upstream.short} — predicted times may be inaccurate. Please double-check departure times.`;
}

function syncVbbWarning() {
    const modalEl = document.getElementById('modal-vbb-warning');
    const scheduleEl = document.getElementById('schedule-vbb-warning');
    const vbbDown = !!(state.lastData?.used_fallback || state.lastError);
    const diag = state.lastError?.err?.serverDiagnostics ?? state.lastData?.diagnostics;
    const message = vbbDown ? vbbWarningMessage(diag) : '';
    for (const el of [modalEl, scheduleEl]) {
        if (!el) continue;
        el.hidden = !vbbDown;
        if (vbbDown) el.textContent = message;
    }
}

/** Floor-minute departures need +59s so Math.floor matches the badge on open. */
function departureMsFromFloorMinutes(dep, nowMs = Date.now()) {
    return nowMs + dep.minutes * 60_000 + FLOOR_MINUTES_OFFSET_MS;
}

function openZoom(dep, arrow) {
    unlockAudio();  // must happen inside tap handler so iOS allows audio
    zoom.active = true;
    zoom.departureTime = departureMsFromFloorMinutes(dep);
    zoom.alarmArmed = dep.minutes > 7;      // arm alarm threshold (7 min)
    zoom.autoCloseArmed = dep.minutes > 5;  // arm auto-dismiss threshold (5 min)

    // Arrow
    const arrowEl = document.getElementById('modal-arrow');
    if (arrowEl) arrowEl.textContent = arrow ?? '';

    // Line chip
    const lineKey = dep.line.replace(/[^A-Z0-9]/g, '');
    const chipEl = document.getElementById('modal-line-chip');
    if (chipEl) {
        chipEl.className = `line-badge line-${lineKey} modal-line-chip`;
        chipEl.textContent = dep.line;
    }

    // Reset alarm styling and re-trigger card entry animation
    const card = document.getElementById('modal-card');
    if (card) {
        card.classList.remove('alarming');
        card.style.animation = 'none';
        void card.offsetWidth;  // force reflow so animation restarts
        card.style.animation = '';
    }

    // Already urgent — enter alarm state immediately instead of waiting for threshold
    if (dep.minutes <= 7) {
        card?.classList.add('alarming');
        playAlarm();
    }

    updateZoomDisplay();
    syncVbbWarning();

    const overlay = document.getElementById('modal-overlay');
    if (overlay) {
        overlay.classList.remove('closing');
        overlay.hidden = false;
    }
}

function closeOverlay(overlayEl, { beforeClose } = {}) {
    if (!overlayEl || overlayEl.hidden) return;
    beforeClose?.();
    overlayEl.classList.add('closing');
    setTimeout(() => {
        overlayEl.hidden = true;
        overlayEl.classList.remove('closing');
    }, OVERLAY_CLOSE_MS);
}

function closeZoom() {
    closeOverlay(document.getElementById('modal-overlay'), {
        beforeClose: () => {
            stopAlarm();
            zoom.active = false;
            zoom.alarmArmed = false;
            zoom.autoCloseArmed = false;
            document.getElementById('modal-card')?.classList.remove('alarming');
        },
    });
}

// =============================================================================
// Rendering
// =============================================================================

/** Format minutes as a clock time by adding them to now. */
function minutesToClockTime(minutes) {
    const t = new Date(Date.now() + minutes * 60_000);
    return t.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}

/** Build a single departure badge element. */
function createDepartureBadge(dep, delay, arrow) {
    const badge = document.createElement('div');
    badge.className = 'departure-badge';
    if (delay > 0) badge.style.animationDelay = `${delay}ms`;

    const clock = document.createElement('span');
    clock.className = 'departure-clock';
    clock.textContent = minutesToClockTime(dep.minutes);

    const mins = document.createElement('span');
    mins.className = 'departure-minutes';
    mins.textContent = dep.minutes;
    mins.setAttribute('aria-label', `${dep.minutes} minutes`);

    const chip = document.createElement('span');
    const lineKey = dep.line.replace(/[^A-Z0-9]/g, '');
    chip.className = `line-badge line-${lineKey} departure-line-chip`;
    chip.textContent = dep.line;

    if (dep.minutes <= 7) badge.classList.add('urgent');
    badge.append(clock, mins, chip);
    badge.addEventListener('click', () => openZoom(dep, arrow));
    return badge;
}

/**
 * Build a quadrant <div> from quadrant data.
 * quadrantIndex (0-3) drives the cascade stagger so cards reveal
 * top-left → top-right → bottom-left → bottom-right.
 */
function createQuadrant(quadrant, quadrantIndex) {
    const el = document.createElement('div');
    el.className = 'quadrant';
    el.setAttribute('aria-label', `${quadrant.label} ${quadrant.arrow}`);

    // Header: arrow + label
    const header = document.createElement('div');
    header.className = 'quadrant-header';

    const arrow = document.createElement('span');
    arrow.className = 'quadrant-arrow';
    arrow.textContent = quadrant.arrow;
    arrow.setAttribute('aria-hidden', 'true');

    const label = document.createElement('span');
    label.className = 'quadrant-label';
    label.textContent = quadrant.label;

    header.append(arrow, label);
    el.appendChild(header);

    // Departures row
    const row = document.createElement('div');
    row.className = 'departures-row';

    if (quadrant.departures.length === 0) {
        const empty = document.createElement('span');
        empty.className = 'departure-empty';
        empty.textContent = '—';
        empty.setAttribute('aria-label', 'No departures');
        // Animate the dash in too
        empty.style.animation = 'flipIn 0.63s cubic-bezier(0.25, 0.46, 0.45, 0.94) both';
        empty.style.animationDelay = `${quadrantIndex * 80}ms`;
        row.appendChild(empty);
    } else {
        quadrant.departures.forEach((dep, i) => {
            // Quadrant cascade: each quadrant starts 80 ms after the previous one.
            // Within the quadrant, each badge is 110 ms after the last.
            const delay = quadrantIndex * 80 + i * 110;
            row.appendChild(createDepartureBadge(dep, delay, quadrant.arrow));
        });
    }

    el.appendChild(row);
    return el;
}

/** Render all four quadrants into the grid. */
function renderQuadrants(data) {
    const grid = document.getElementById('display-grid');
    if (!grid) return;

    const titleEl = document.getElementById('station-title');
    if (titleEl) titleEl.textContent = data.station_name;

    updateDisplayStatus();

    grid.innerHTML = '';
    (data.quadrants || []).forEach((q, i) => grid.appendChild(createQuadrant(q, i)));
}

function recordFetchError(err, copy) {
    state.lastError = {
        ...copy,
        at: Date.now(),
        err,
        consecutiveFailures: (state.lastError?.consecutiveFailures ?? 0) + 1,
    };
}

/**
 * Render a full-grid error state.
 * If we already have good data, leave the quadrants intact and only
 * update the stale badge — aged departures are pruned separately.
 * @param {{ title: string, subtitle: string, badge: string }} copy
 * @param {boolean} isHard - true when no prior data exists (show full error card)
 */
function showError(copy, isHard = false) {
    console.error(`🚆 ${copy.title} — ${copy.subtitle}`);

    // Soft error: keep last good data on screen, prune departed trains, update status
    if (state.lastData && !isHard) {
        updateDisplayStatus();
        renderAgedQuadrantsIfNeeded();
        return;
    }

    // Hard error (no data ever fetched): show a clear error card spanning the grid
    const grid = document.getElementById('display-grid');
    if (!grid) return;
    grid.innerHTML = '';

    // Single card spanning all four grid cells
    const card = document.createElement('div');
    card.className = 'error-card';
    card.setAttribute('role', 'alert');
    card.style.cssText = 'grid-column: 1 / -1; grid-row: 1 / -1;';

    const icon = document.createElement('span');
    icon.className = 'error-card__icon';
    icon.textContent = '⚠';
    icon.setAttribute('aria-hidden', 'true');

    const title = document.createElement('p');
    title.className = 'error-card__title';
    title.textContent = copy.title;

    const sub = document.createElement('p');
    sub.className = 'error-card__sub';
    sub.textContent = `${copy.subtitle} · Retrying every 30s`;

    card.append(icon, title, sub);
    grid.appendChild(card);
    updateDisplayStatus();
}

// =============================================================================
// Diagnostics modal (stale / error badge tap)
// =============================================================================

function formatDiagnosticsValue(value) {
    if (value == null) return '—';
    if (typeof value === 'object') return JSON.stringify(value, null, 2);
    return String(value);
}

function buildDiagnosticLines() {
    const lines = [];
    const fetchError = state.lastError;
    const raw = fetchError?.err;
    const diag = raw?.serverDiagnostics ?? state.lastData?.diagnostics;

    if (fetchError) {
        lines.push(['Issue', fetchError.title]);
        lines.push(['Summary', fetchError.subtitle]);
        if (raw?.httpStatus != null) lines.push(['HTTP status', String(raw.httpStatus)]);
        if (raw?.serverError) lines.push(['Server error', raw.serverError]);
        if (raw?.serverDetail) lines.push(['Server detail', raw.serverDetail]);
        if (raw?.message && raw.message !== raw.serverError) lines.push(['Message', raw.message]);
        lines.push(['Consecutive failures', String(fetchError.consecutiveFailures)]);
        if (fetchError.at) {
            lines.push(['Last failure', new Date(fetchError.at).toLocaleString('de-DE', { timeZone: BERLIN_TZ })]);
        }
    } else if (state.lastData?.used_fallback) {
        const upstream = describeVbbUpstream(state.lastData.diagnostics);
        lines.push(['Issue', 'Serving stale snapshot']);
        lines.push(['Summary', `${upstream.short} — departures time-shifted from last successful fetch`]);
    } else {
        lines.push(['Issue', 'No active fetch error']);
    }

    if (diag) {
        if (diag.station_id) lines.push(['Station ID', diag.station_id]);
        if (diag.vbb_error_summary) lines.push(['VBB failure', diag.vbb_error_summary]);
        if (diag.vbb_error_kind) lines.push(['VBB error kind', diag.vbb_error_kind]);
        if (diag.vbb_http_status != null) lines.push(['VBB HTTP status', String(diag.vbb_http_status)]);
        if (diag.vbb_error) lines.push(['VBB error detail', diag.vbb_error]);
        if (diag.snapshot) {
            lines.push(['Snapshot age', diag.snapshot.snapshot_age ?? '—']);
            lines.push(['Snapshot captured', diag.snapshot.captured_at ?? '—']);
            lines.push(['Snapshot departures', String(diag.snapshot.departure_count ?? '—')]);
        }
    }

    if (state.lastUpdatedAt) {
        lines.push(['Last successful fetch', formatRelativeAgo(state.lastUpdatedAt)]);
        lines.push(['Last fetch at', new Date(state.lastUpdatedAt).toLocaleString('de-DE', { timeZone: BERLIN_TZ })]);
    }
    if (state.lastData?.timestamp) lines.push(['Server timestamp', state.lastData.timestamp]);
    if (state.lastData?.used_fallback != null) lines.push(['used_fallback', String(state.lastData.used_fallback)]);
    lines.push(['Display status', resolveDisplayStatus()]);

    lines.push(['Client fetch timeout', `${DISPLAY_CONFIG.FETCH_TIMEOUT_MS / 1000}s`]);
    lines.push(['Poll interval', `${DISPLAY_CONFIG.REFRESH_INTERVAL_MS / 1000}s`]);
    lines.push(['Endpoint', 'GET /api/display/data']);

    return lines;
}

function openDiagnosticsModal() {
    const overlay = document.getElementById('diagnostics-overlay');
    const body = document.getElementById('diagnostics-body');
    if (!overlay || !body) return;

    body.innerHTML = '';
    for (const [label, value] of buildDiagnosticLines()) {
        const row = document.createElement('div');
        row.className = 'diagnostics-row';

        const labelEl = document.createElement('dt');
        labelEl.className = 'diagnostics-row__label';
        labelEl.textContent = label;

        const valueEl = document.createElement('dd');
        valueEl.className = 'diagnostics-row__value';
        valueEl.textContent = formatDiagnosticsValue(value);

        row.append(labelEl, valueEl);
        body.appendChild(row);
    }

    overlay.hidden = false;
    overlay.classList.remove('closing');
}

function closeDiagnosticsModal() {
    closeOverlay(document.getElementById('diagnostics-overlay'));
}

// =============================================================================
// Data loop
// =============================================================================

function rebuildQuadrantIndex(data) {
    state.quadrantsByKey = new Map((data?.quadrants ?? []).map(q => [q.key, q]));
}

async function refresh() {
    if (refreshInFlight) return;
    refreshInFlight = true;
    try {
        const data = await fetchDisplayData();
        state.lastData = data;
        state.lastError = null;
        rebuildQuadrantIndex(data);
        warnedMissingQuadrantKeys.clear();
        state.lastUpdatedAt = Date.now();
        state.lastAgedElapsedMin = null;
        state.lastRenderedSnapshot = departuresSnapshot(data.quadrants);
        renderQuadrants(data);
        console.info(`[refresh] updated — station: ${data.station_name}, stale: ${data.used_fallback}`);
    } catch (err) {
        const copy = describeDisplayFetchError(err);
        recordFetchError(err, copy);
        // Hard error only when we've never had good data; otherwise keep last display intact
        showError(copy, /* isHard */ !state.lastData);
        updateDisplayStatus();
    } finally {
        refreshInFlight = false;
    }
    // Run outside the try/catch so schedule errors don't masquerade as fetch failures.
    evaluateSchedules();
}

// =============================================================================
// Boot
// =============================================================================

window.addEventListener('DOMContentLoaded', () => {
    loadSchedules();
    updateClock();
    updateDisplayStatus();
    setInterval(() => {
        updateClock();
        updateDisplayStatus();
        updateZoomDisplay();
        renderAgedQuadrantsIfNeeded();
        evaluateSchedules();
    }, DISPLAY_CONFIG.CLOCK_INTERVAL_MS);

    // Restore mute state on load
    const muteBtn = document.getElementById('mute-btn');
    if (muteBtn) {
        muteBtn.classList.toggle('muted', muted);
        muteBtn.setAttribute('aria-pressed', String(muted));
        muteBtn.setAttribute('aria-label', muted ? 'Unmute alarm' : 'Mute alarm');
        muteBtn.addEventListener('click', toggleMute);
    }

    // Close zoom modal on backdrop click
    document.getElementById('modal-overlay')?.addEventListener('click', e => {
        if (e.target === e.currentTarget) closeZoom();
    });

    // Close zoom modal on close button click
    document.getElementById('modal-close-btn')?.addEventListener('click', closeZoom);

    // Close modals on Escape
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            closeZoom();
            closeScheduleModal();
            closeDiagnosticsModal();
        }
    });

    document.getElementById('stale-badge')?.addEventListener('click', e => {
        const badge = e.currentTarget;
        if (!badge.classList.contains('visible')) return;
        openDiagnosticsModal();
    });
    document.getElementById('diagnostics-close-btn')?.addEventListener('click', closeDiagnosticsModal);
    document.getElementById('diagnostics-overlay')?.addEventListener('click', e => {
        if (e.target === e.currentTarget) closeDiagnosticsModal();
    });

    // Schedule button
    document.getElementById('schedule-btn')?.addEventListener('click', () => {
        unlockAudio();  // user gesture — unlock audio for future auto-zoom alarms
        openScheduleModal();
    });

    // Schedule modal backdrop click
    document.getElementById('schedule-overlay')?.addEventListener('click', e => {
        if (e.target === e.currentTarget) closeScheduleModal();
    });

    // Schedule modal cancel / save
    document.getElementById('schedule-cancel-btn')?.addEventListener('click', closeScheduleModal);
    document.getElementById('schedule-save-btn')?.addEventListener('click', saveSchedule);

    // Switch-offer dismiss — suppress this train so it isn't re-offered next tick
    document.getElementById('switch-offer-dismiss')?.addEventListener('click', () => {
        if (switchOffer) dismissedOfferKeys.add(switchOffer.key);
        hideSwitchOffer();
    });

    // Initial schedule badge render from localStorage
    renderScheduleBadges();

    // Initial load then recurring poll
    refresh();
    setInterval(refresh, DISPLAY_CONFIG.REFRESH_INTERVAL_MS);
});

// =============================================================================
// Scheduler — state & persistence
// =============================================================================

/**
 * In-memory array of schedule objects:
 * {
 *   id: string,
 *   targetMinutes: number,            — minutes from midnight (0–1439)
 *   targetDate: string,               — 'YYYY-MM-DD' Berlin calendar day
 *   repeatDays: number[],             — JS dow values (0=Sun…6=Sat); [] = one-time
 *   quadrantKey: string,
 *   label: string,
 *   arrow: string,
 *   lineFilter: string|null,          — single line within the group, or null for all
 *   toleranceMinutes: number,         — ± window half-width around targetMinutes
 *   activeDepartureKey: string|null,
 * }
 */
let schedules = [];

const berlinDateTimeFormat = new Intl.DateTimeFormat('en-US', {
    timeZone: BERLIN_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
});

function berlinParts(date = new Date()) {
    const parts = Object.fromEntries(
        berlinDateTimeFormat
            .formatToParts(date)
            .filter(part => part.type !== 'literal')
            .map(part => [part.type, part.value]),
    );
    return {
        year: Number(parts.year),
        month: Number(parts.month),
        day: Number(parts.day),
        hours: Number(parts.hour),
        minutes: Number(parts.minute),
    };
}

function berlinDateString(date = new Date()) {
    const { year, month, day } = berlinParts(date);
    return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function berlinNowMinutes(date = new Date()) {
    const { hours, minutes } = berlinParts(date);
    return hours * 60 + minutes;
}

function berlinTomorrowDateString(date = new Date()) {
    return berlinDateString(new Date(date.getTime() + 86_400_000));
}

/** Berlin calendar date i days from now (handles DST by anchoring to noon UTC). */
function berlinDatePlusDays(i) {
    const { year, month, day } = berlinParts();
    return berlinDateString(new Date(Date.UTC(year, month - 1, day + i, 12, 0, 0)));
}

/** Berlin day-of-week (0=Sun … 6=Sat, JS standard) derived from the Berlin calendar date. */
function berlinDayOfWeek(date = new Date()) {
    const { year, month, day } = berlinParts(date);
    return new Date(year, month - 1, day).getDay();
}

/** If target time has already passed today in Berlin, schedule for tomorrow. */
function resolveTargetDate(targetMinutes) {
    if (targetMinutes <= berlinNowMinutes()) {
        return berlinTomorrowDateString();
    }
    return berlinDateString();
}

/**
 * Next Berlin date (YYYY-MM-DD) on which any of repeatDays fires.
 * Checks today first (if targetMinutes hasn't passed yet), then searches up to 7 days ahead.
 */
function nextRepeatDate(repeatDays, targetMinutes) {
    const todayDow = berlinDayOfWeek();
    if (repeatDays.includes(todayDow) && targetMinutes > berlinNowMinutes()) {
        return berlinDateString();
    }
    for (let i = 1; i <= 7; i++) {
        if (repeatDays.includes((todayDow + i) % 7)) {
            return berlinDatePlusDays(i);
        }
    }
    throw new Error(`nextRepeatDate: repeatDays is empty or unreachable`);
}

function berlinTargetMs(targetDate, targetMinutes) {
    const [year, month, day] = targetDate.split('-').map(Number);
    const hour = Math.floor(targetMinutes / 60);
    const minute = targetMinutes % 60;
    // Scan ±3h around the naïve UTC equivalent to cover CET (+1) and CEST (+2).
    const naiveUtcMs = Date.UTC(year, month - 1, day, hour, minute, 0);
    for (let ms = naiveUtcMs - 3 * 3600_000; ms <= naiveUtcMs + 3 * 3600_000; ms += 60_000) {
        const p = berlinParts(new Date(ms));
        if (p.year === year && p.month === month && p.day === day && p.hours === hour && p.minutes === minute) {
            return ms;
        }
    }
    throw new Error(`Unresolved Berlin target: ${targetDate} ${hour}:${minute}`);
}

function scheduleTargetMs(schedule) {
    const targetDate = schedule.repeatDays?.length
        ? nextRepeatDate(schedule.repeatDays, schedule.targetMinutes)
        : schedule.targetDate;
    return berlinTargetMs(targetDate, schedule.targetMinutes);
}

function loadSchedules() {
    try {
        const raw = localStorage.getItem(SCHEDULE_CONFIG.LS_KEY);
        schedules = raw ? JSON.parse(raw) : [];
    } catch (err) {
        console.error('🗓 failed to load schedules', err);
        schedules = [];
    }
    const today = berlinDateString();
    schedules = schedules.map(s => ({
        ...s,
        targetDate: s.targetDate ?? today,
        repeatDays: s.repeatDays ?? [],
        toleranceMinutes: s.toleranceMinutes ?? SCHEDULE_CONFIG.DEFAULT_TOLERANCE_MIN,
    }));
}

function saveSchedulesToStorage() {
    try {
        localStorage.setItem(SCHEDULE_CONFIG.LS_KEY, JSON.stringify(schedules));
    } catch (err) {
        console.error('🗓 failed to persist schedules', err);
    }
}

function removeSchedule(id) {
    schedules = schedules.filter(s => s.id !== id);
    saveSchedulesToStorage();
    renderScheduleBadges();
}

// =============================================================================
// Scheduler — badge strip
// =============================================================================

function formatScheduleLabel(schedule) {
    const h = String(Math.floor(schedule.targetMinutes / 60)).padStart(2, '0');
    const m = String(schedule.targetMinutes % 60).padStart(2, '0');
    const lineStr = schedule.lineFilter ?? schedule.label;
    const parts = [`${h}:${m} ${lineStr} ${schedule.arrow}`];

    if (schedule.repeatDays?.length) {
        parts.push(DAYS_OF_WEEK.filter(d => schedule.repeatDays.includes(d.dow)).map(d => d.label).join(' '));
    } else {
        const today = berlinDateString();
        if (schedule.targetDate && schedule.targetDate !== today) {
            parts.push(schedule.targetDate === berlinTomorrowDateString() ? 'tomorrow' : schedule.targetDate);
        }
    }

    parts.push(`±${schedule.toleranceMinutes ?? SCHEDULE_CONFIG.DEFAULT_TOLERANCE_MIN}m`);

    return parts.join(' · ');
}

function renderScheduleBadges() {
    const container = document.getElementById('schedule-badges');
    if (!container) return;
    container.innerHTML = '';

    for (const schedule of schedules) {
        const label = formatScheduleLabel(schedule);
        const badge = document.createElement('div');
        badge.className = 'schedule-badge';
        badge.dataset.scheduleId = schedule.id;

        const editBtn = document.createElement('button');
        editBtn.className = 'schedule-badge__edit';
        editBtn.type = 'button';
        editBtn.setAttribute('aria-label', `Edit reminder ${label}`);
        editBtn.textContent = label;
        editBtn.addEventListener('click', () => openScheduleModal(schedule.id));

        const removeBtn = document.createElement('button');
        removeBtn.className = 'schedule-badge__remove';
        removeBtn.type = 'button';
        removeBtn.setAttribute('aria-label', `Remove reminder ${label}`);
        removeBtn.textContent = '×';
        removeBtn.addEventListener('click', () => removeSchedule(schedule.id));

        badge.append(editBtn, removeBtn);
        container.appendChild(badge);
    }
}

// =============================================================================
// Scheduler — modal
// =============================================================================

let scheduleModalEditId = null;
let scheduleModalSelectedQuadrantKey = null;  // resolved from the route wheels; null when invalid
let scheduleModalSelectedDays = new Set();
let scheduleModalLineFilter = null;           // null = whole group/quadrant

// Route wheel options, rebuilt from live quadrant data when the modal opens.
let scheduleDirectionOptions = [];  // [{ arrow, label }]
let scheduleLineOptions = [];       // [string] — group labels first, then individual lines

function berlinNow() {
    const { hours, minutes } = berlinParts();
    return { hours, minutes };
}

/** Populate a wheel element with one item per label string. */
function fillWheel(wheelEl, labels) {
    wheelEl.innerHTML = '';
    labels.forEach((label, i) => {
        const item = document.createElement('div');
        item.className = 'schedule-wheel-item';
        item.dataset.index = i;
        item.setAttribute('role', 'option');
        item.textContent = label;
        wheelEl.appendChild(item);
    });
}

/** Numeric wheel labels 0..count-1, zero-padded to padLen. */
function numberWheelLabels(count, padLen) {
    return Array.from({ length: count }, (_, i) => String(i).padStart(padLen, '0'));
}

function scrollWheelTo(wheelEl, value) {
    wheelEl.scrollTop = value * WHEEL_ITEM_PX;
}

function readWheelValue(wheelEl, count) {
    const index = Math.round(wheelEl.scrollTop / WHEEL_ITEM_PX);
    return Math.min(count - 1, Math.max(0, index));
}

function readSelectedTargetMinutes() {
    const hourWheel = document.getElementById('schedule-wheel-hour');
    const minWheel = document.getElementById('schedule-wheel-minute');
    const hours = hourWheel ? readWheelValue(hourWheel, 24) : 0;
    const minutes = minWheel ? readWheelValue(minWheel, 60) : 0;
    return hours * 60 + minutes;
}

function updateScheduleDateHint() {
    const hint = document.getElementById('schedule-date-hint');
    if (!hint) return;
    if (scheduleModalSelectedDays.size > 0) {
        const dayStr = DAYS_OF_WEEK
            .filter(d => scheduleModalSelectedDays.has(d.dow))
            .map(d => d.label)
            .join(' ');
        hint.textContent = `Repeats: ${dayStr}`;
        return;
    }
    const targetMinutes = readSelectedTargetMinutes();
    const targetDate = resolveTargetDate(targetMinutes);
    hint.textContent = targetDate === berlinDateString() ? 'Today' : 'Tomorrow';
}

let scheduleDateHintRafId = null;

function throttledUpdateScheduleDateHint() {
    if (scheduleDateHintRafId !== null) return;
    scheduleDateHintRafId = requestAnimationFrame(() => {
        scheduleDateHintRafId = null;
        updateScheduleDateHint();
    });
}

// =============================================================================
// Scheduler — tolerance & route wheels
// =============================================================================

function buildToleranceWheel(toleranceMinutes) {
    const wheel = document.getElementById('schedule-wheel-tolerance');
    if (!wheel) return;
    fillWheel(wheel, numberWheelLabels(SCHEDULE_CONFIG.MAX_TOLERANCE_MIN + 1, 1));
    requestAnimationFrame(() => scrollWheelTo(wheel, toleranceMinutes));
}

function readToleranceMinutes() {
    const wheel = document.getElementById('schedule-wheel-tolerance');
    if (!wheel) return SCHEDULE_CONFIG.DEFAULT_TOLERANCE_MIN;
    return readWheelValue(wheel, SCHEDULE_CONFIG.MAX_TOLERANCE_MIN + 1);
}

/** Derive direction + line wheel options from live quadrant data (encounter order). */
function buildRouteOptions() {
    const quadrants = state.lastData?.quadrants ?? [];

    const arrows = [];
    for (const q of quadrants) {
        if (!arrows.includes(q.arrow)) arrows.push(q.arrow);
    }
    scheduleDirectionOptions = arrows.map(arrow => ({ arrow, label: DIRECTION_LABELS[arrow] ?? arrow }));

    const groups = [];
    for (const q of quadrants) {
        if (!groups.includes(q.label)) groups.push(q.label);
    }
    const lines = [];
    for (const q of quadrants) {
        for (const line of (q.lines ?? [])) {
            if (!lines.includes(line)) lines.push(line);
        }
    }
    lines.sort();
    // Group labels ("S1/26") first, then individual lines ("S1", "S25", …).
    scheduleLineOptions = [...groups, ...lines];
}

/**
 * Resolve a (line, direction) pair to a real quadrant.
 * A group label matches the whole quadrant; an individual line sets a line filter.
 * Returns null when no quadrant carries that line in that direction (e.g. "S1/26 Clockwise").
 */
function resolveRouteSelection(lineValue, arrow) {
    const quadrants = state.lastData?.quadrants ?? [];
    let q = quadrants.find(x => x.arrow === arrow && x.label === lineValue);
    if (q) return { quadrantKey: q.key, lineFilter: null };
    q = quadrants.find(x => x.arrow === arrow && (x.lines ?? []).includes(lineValue));
    if (q) return { quadrantKey: q.key, lineFilter: lineValue };
    return null;
}

function readRouteSelection() {
    const dirWheel = document.getElementById('schedule-wheel-direction');
    const lineWheel = document.getElementById('schedule-wheel-line');
    const dirIdx = dirWheel ? readWheelValue(dirWheel, scheduleDirectionOptions.length) : 0;
    const lineIdx = lineWheel ? readWheelValue(lineWheel, scheduleLineOptions.length) : 0;
    return {
        arrow: scheduleDirectionOptions[dirIdx]?.arrow ?? null,
        lineValue: scheduleLineOptions[lineIdx] ?? null,
    };
}

/** Validate current route wheels, toggle save + error, and cache the resolved selection. */
function validateRoute() {
    const errEl = document.getElementById('schedule-route-error');
    const saveBtn = document.getElementById('schedule-save-btn');

    if (scheduleLineOptions.length === 0 || scheduleDirectionOptions.length === 0) {
        scheduleModalSelectedQuadrantKey = null;
        scheduleModalLineFilter = null;
        if (errEl) { errEl.hidden = false; errEl.textContent = 'Waiting for live data…'; }
        if (saveBtn) saveBtn.disabled = true;
        return;
    }

    const { arrow, lineValue } = readRouteSelection();
    const resolved = (arrow && lineValue) ? resolveRouteSelection(lineValue, arrow) : null;

    if (resolved) {
        scheduleModalSelectedQuadrantKey = resolved.quadrantKey;
        scheduleModalLineFilter = resolved.lineFilter;
        if (errEl) errEl.hidden = true;
        if (saveBtn) saveBtn.disabled = false;
    } else {
        scheduleModalSelectedQuadrantKey = null;
        scheduleModalLineFilter = null;
        const dirLabel = DIRECTION_LABELS[arrow] ?? arrow ?? '';
        if (errEl) { errEl.hidden = false; errEl.textContent = `${lineValue ?? ''} ${dirLabel} isn’t a valid route`; }
        if (saveBtn) saveBtn.disabled = true;
    }
}

let validateRouteRafId = null;

function throttledValidateRoute() {
    if (validateRouteRafId !== null) return;
    validateRouteRafId = requestAnimationFrame(() => {
        validateRouteRafId = null;
        validateRoute();
    });
}

/** Build both route wheels, seeding selection from the schedule being edited (if any). */
function buildRouteWheels(editing) {
    buildRouteOptions();
    const dirWheel = document.getElementById('schedule-wheel-direction');
    const lineWheel = document.getElementById('schedule-wheel-line');
    if (!dirWheel || !lineWheel) return;

    fillWheel(dirWheel, scheduleDirectionOptions.map(o => o.label));
    fillWheel(lineWheel, scheduleLineOptions);

    let dirIdx = 0;
    let lineIdx = 0;
    if (editing) {
        const di = scheduleDirectionOptions.findIndex(o => o.arrow === editing.arrow);
        if (di !== -1) dirIdx = di;
        const li = scheduleLineOptions.indexOf(editing.lineFilter ?? editing.label);
        if (li !== -1) lineIdx = li;
    }

    dirWheel.onscroll = throttledValidateRoute;
    lineWheel.onscroll = throttledValidateRoute;
    requestAnimationFrame(() => {
        scrollWheelTo(dirWheel, dirIdx);
        scrollWheelTo(lineWheel, lineIdx);
    });

    validateRoute();
}

function buildDayPicker() {
    const grid = document.getElementById('schedule-day-grid');
    if (!grid) return;
    grid.innerHTML = '';

    for (const { dow, label } of DAYS_OF_WEEK) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'schedule-day-btn';
        btn.textContent = label;
        btn.setAttribute('aria-pressed', String(scheduleModalSelectedDays.has(dow)));
        if (scheduleModalSelectedDays.has(dow)) btn.classList.add('selected');

        btn.addEventListener('click', () => {
            if (scheduleModalSelectedDays.has(dow)) {
                scheduleModalSelectedDays.delete(dow);
                btn.classList.remove('selected');
                btn.setAttribute('aria-pressed', 'false');
            } else {
                scheduleModalSelectedDays.add(dow);
                btn.classList.add('selected');
                btn.setAttribute('aria-pressed', 'true');
            }
            updateScheduleDateHint();
        });

        grid.appendChild(btn);
    }
}

function openScheduleModal(editId = null) {
    const overlay = document.getElementById('schedule-overlay');
    if (!overlay) return;

    const editing = editId ? schedules.find(s => s.id === editId) : null;

    scheduleModalEditId = editId ?? null;
    scheduleModalSelectedQuadrantKey = editing?.quadrantKey ?? null;
    scheduleModalSelectedDays = new Set(editing?.repeatDays ?? []);
    scheduleModalLineFilter = editing?.lineFilter ?? null;

    const title = document.getElementById('schedule-modal-title');
    if (title) title.textContent = editing ? 'Edit Reminder' : 'Schedule Reminder';

    const saveBtn = document.getElementById('schedule-save-btn');
    if (saveBtn) saveBtn.textContent = editing ? 'Update' : 'Save';
    // validateRoute() (called via buildRouteWheels) sets the final disabled state.

    // Build wheels
    const hourWheel = document.getElementById('schedule-wheel-hour');
    const minWheel = document.getElementById('schedule-wheel-minute');
    if (hourWheel) fillWheel(hourWheel, numberWheelLabels(24, 2));
    if (minWheel) fillWheel(minWheel, numberWheelLabels(60, 2));

    const { hours, minutes } = editing
        ? { hours: Math.floor(editing.targetMinutes / 60), minutes: editing.targetMinutes % 60 }
        : berlinNow();

    if (hourWheel) {
        hourWheel.onscroll = throttledUpdateScheduleDateHint;
        requestAnimationFrame(() => scrollWheelTo(hourWheel, hours));
    }
    if (minWheel) {
        minWheel.onscroll = throttledUpdateScheduleDateHint;
        requestAnimationFrame(() => scrollWheelTo(minWheel, minutes));
    }

    buildToleranceWheel(editing?.toleranceMinutes ?? SCHEDULE_CONFIG.DEFAULT_TOLERANCE_MIN);

    updateScheduleDateHint();
    buildDayPicker();
    buildRouteWheels(editing);

    syncVbbWarning();

    overlay.hidden = false;
    overlay.classList.remove('closing');
    requestAnimationFrame(() => saveBtn?.focus());
}

function closeScheduleModal() {
    closeOverlay(document.getElementById('schedule-overlay'));
}

function saveSchedule() {
    if (!scheduleModalSelectedQuadrantKey) return;

    unlockAudio();

    const targetMinutes = readSelectedTargetMinutes();
    const repeatDays = [...scheduleModalSelectedDays];
    // One-time schedules store a concrete date; repeating schedules compute it dynamically.
    const targetDate = repeatDays.length ? null : resolveTargetDate(targetMinutes);

    const quadrantData = state.quadrantsByKey.get(scheduleModalSelectedQuadrantKey)
        ?? state.lastData?.quadrants?.find(q => q.key === scheduleModalSelectedQuadrantKey);
    if (!quadrantData) return;

    const fields = {
        targetMinutes,
        targetDate,
        repeatDays,
        quadrantKey: quadrantData.key,
        label: quadrantData.label,
        arrow: quadrantData.arrow,
        lineFilter: scheduleModalLineFilter,
        toleranceMinutes: readToleranceMinutes(),
        activeDepartureKey: null,
    };

    if (scheduleModalEditId) {
        const idx = schedules.findIndex(s => s.id === scheduleModalEditId);
        if (idx !== -1) schedules[idx] = { ...schedules[idx], ...fields };
    } else {
        schedules.push({ id: crypto.randomUUID(), ...fields });
    }

    saveSchedulesToStorage();
    renderScheduleBadges();
    closeScheduleModal();
    const saved = scheduleModalEditId
        ? schedules.find(s => s.id === scheduleModalEditId)
        : schedules[schedules.length - 1];
    console.info(`🗓 Schedule ${scheduleModalEditId ? 'updated' : 'saved'}: ${formatScheduleLabel(saved)}`);
}

// =============================================================================
// Scheduler — matcher & auto-zoom
// =============================================================================

/**
 * Stable fingerprint for a departure, invariant across per-minute aging.
 *
 * state.quadrantsByKey is rebuilt from aged data so dep.minutes decrements each
 * minute; adding elapsedMin recovers the original fetch-relative minutes, giving
 * a constant key for the same physical train between API fetches.
 */
function stableDepartureKey(dep, nowMs) {
    const elapsedMin = state.lastUpdatedAt != null
        ? Math.floor((nowMs - state.lastUpdatedAt) / 60_000)
        : 0;
    const stableMs = (state.lastUpdatedAt ?? nowMs) + (dep.minutes + elapsedMin) * 60_000 + FLOOR_MINUTES_OFFSET_MS;
    return `${dep.line}:${stableMs}`;
}

/**
 * Departures in the scheduled quadrant that fall inside the ± window, sorted by
 * closeness to the target (closest first). Empty once the whole window is past.
 *
 * Target time = the train you want (e.g. 10:03).
 * Window = [target − tolerance, target + tolerance].
 */
function candidatesForSchedule(schedule, quadrant, nowMs) {
    const targetMs = scheduleTargetMs(schedule);
    const tolerance = schedule.toleranceMinutes ?? SCHEDULE_CONFIG.DEFAULT_TOLERANCE_MIN;
    const earliestMs = targetMs - tolerance * 60_000;
    const latestMs = targetMs + tolerance * 60_000;

    // The whole ± window has already passed — nothing left to catch.
    if (latestMs <= nowMs) return [];

    const out = [];
    for (const dep of (quadrant.departures ?? [])) {
        if (schedule.lineFilter && dep.line !== schedule.lineFilter) continue;
        // Use raw floor time (no +59s) for window bounds — the offset is only for zoom display alignment.
        const depMs = nowMs + dep.minutes * 60_000;
        if (depMs < earliestMs || depMs > latestMs) continue;
        out.push({ dep, dist: Math.abs(depMs - targetMs) });
    }
    out.sort((a, b) => a.dist - b.dist);
    return out.map(c => c.dep);
}

/**
 * Evaluate all active schedules against current departure data.
 * Called after each successful API refresh and on every clock tick.
 *
 * Behaviour: lock onto the first qualifying train (the one closest to target at
 * first match) and never switch automatically. If a closer train later appears
 * while one is locked, surface a non-blocking "switch" offer instead — the user
 * decides. The locked train is only replaced when it leaves the window (departs).
 */
function evaluateSchedules() {
    if (!state.lastData || schedules.length === 0) return;

    const nowMs = Date.now();

    for (const schedule of schedules) {
        const quadrant = state.quadrantsByKey.get(schedule.quadrantKey);
        if (!quadrant) {
            if (!warnedMissingQuadrantKeys.has(schedule.quadrantKey)) {
                warnedMissingQuadrantKeys.add(schedule.quadrantKey);
                console.warn(`🗓 [eval] no quadrant found for key "${schedule.quadrantKey}" — available: ${[...state.quadrantsByKey.keys()].join(', ')}`);
            }
            continue;
        }

        const candidates = candidatesForSchedule(schedule, quadrant, nowMs);

        if (candidates.length === 0) {
            schedule.activeDepartureKey = null;
            dismissSwitchOffer(schedule.id);
            continue;
        }

        const ideal = candidates[0];  // closest to target
        const idealKey = stableDepartureKey(ideal, nowMs);

        // Is the locked train still in the window?
        const locked = schedule.activeDepartureKey
            ? candidates.find(d => stableDepartureKey(d, nowMs) === schedule.activeDepartureKey)
            : null;

        // Nothing locked yet (first match, or the locked train has departed) →
        // lock onto the closest train and zoom, exactly like a manual tap.
        if (!locked) {
            // Manual zoom open: don't steal it. Leave the key unset so we lock once it closes.
            if (zoom.active) continue;
            schedule.activeDepartureKey = idealKey;
            dismissSwitchOffer(schedule.id);
            console.info(`[evaluateSchedules] auto-zoom for "${formatScheduleLabel(schedule)}" → ${idealKey}`);
            openZoom(ideal, schedule.arrow);
            continue;
        }

        // A train is locked. Never switch automatically — if a closer one exists, offer it.
        if (idealKey !== schedule.activeDepartureKey) {
            showSwitchOffer(schedule, ideal, idealKey);
        } else {
            dismissSwitchOffer(schedule.id);
        }
    }
}

// =============================================================================
// Scheduler — switch offer (non-blocking)
// =============================================================================

let switchOffer = null;                // { scheduleId, key } currently shown, or null
const dismissedOfferKeys = new Set();  // offered trains the user dismissed — don't re-offer

/**
 * Show a small non-blocking toast offering a closer train. Re-callable each tick:
 * keeps the captured departure fresh without re-running the entry animation
 * (setting hidden=false when already visible is a no-op for display).
 */
function showSwitchOffer(schedule, dep, key) {
    if (dismissedOfferKeys.has(key)) return;
    const offer = document.getElementById('switch-offer');
    if (!offer) return;

    switchOffer = { scheduleId: schedule.id, key };

    const timeStr = new Date(departureMsFromFloorMinutes(dep)).toLocaleTimeString('de-DE', {
        hour: '2-digit', minute: '2-digit',
    });
    const labelEl = document.getElementById('switch-offer-label');
    if (labelEl) labelEl.textContent = `${dep.line} · ${timeStr}`;

    const acceptBtn = document.getElementById('switch-offer-accept');
    if (acceptBtn) acceptBtn.onclick = () => acceptSwitchOffer(schedule.id, dep, key);

    offer.hidden = false;
}

/** Accept the offered train: lock it and zoom (the tap is the audio-unlock gesture). */
function acceptSwitchOffer(scheduleId, dep, key) {
    const schedule = schedules.find(s => s.id === scheduleId);
    if (schedule) schedule.activeDepartureKey = key;
    hideSwitchOffer();
    openZoom(dep, schedule?.arrow);
}

function hideSwitchOffer() {
    switchOffer = null;
    const offer = document.getElementById('switch-offer');
    if (offer) offer.hidden = true;
}

/** Hide the offer if it belongs to this schedule (or unconditionally when no id given). */
function dismissSwitchOffer(scheduleId) {
    if (!switchOffer) return;
    if (scheduleId && switchOffer.scheduleId !== scheduleId) return;
    hideSwitchOffer();
}
