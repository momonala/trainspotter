'use strict';

// =============================================================================
// Config
// =============================================================================

const DISPLAY_CONFIG = {
    FETCH_TIMEOUT_MS:   18000,
    REFRESH_INTERVAL_MS: 30000,
    CLOCK_INTERVAL_MS:   1000,
};

// =============================================================================
// State
// =============================================================================

const state = {
    lastData: null,
    lastUpdatedAt: null,
};

// Zoom modal state
const zoom = {
    active: false,
    departureTime: null,  // absolute ms — computed when zoom opens
    line: null,
    alarmArmed: false,    // flips false once alarm fires (threshold: 7 min)
    autoCloseArmed: false,// flips false once auto-dismiss fires (threshold: 5 min)
};

let alarmTimerId = null;
let alarmAutoStopId = null;  // clears the alarm sound after 60 s
let audioCtx = null;         // shared context — must be created inside a user gesture
let muted = localStorage.getItem('alarmMuted') === 'true';

// =============================================================================
// API
// =============================================================================

/**
 * Map a failed display fetch to engineering-facing copy.
 * Backend /api/display/data only returns HTTP errors as 502 or 500 (see src/app.py).
 *
 * @returns {{ title: string, subtitle: string, badge: string }}
 */
function describeDisplayFetchError(err) {
    const status = err.httpStatus != null ? Number(err.httpStatus) : null;

    if (err.name === 'AbortError') {
        return {
            title: `Client timeout (${DISPLAY_CONFIG.FETCH_TIMEOUT_MS / 1000}s)`,
            subtitle: 'GET /api/display/data aborted — server may still log 502 if VBB is slow',
            badge: '⚠\u2009fetch timeout',
        };
    }

    if (status === 502) {
        const detail = err.serverDetail || 'No cached snapshot available';
        return {
            title: '502 · VBB unreachable',
            subtitle: [err.serverError, detail].filter(Boolean).join(' — '),
            badge: '⚠\u2009502 · VBB down',
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
// Clock & last-updated ticker
// =============================================================================

function updateClock() {
    const el = document.getElementById('header-time');
    if (!el) return;
    el.textContent = new Date().toLocaleTimeString('de-DE', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

function formatAgo(fromDate) {
    if (!fromDate) return '';
    let secs = Math.max(0, Math.floor((Date.now() - fromDate) / 1000));
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    const pad = n => String(n).padStart(2, '0');
    const label = m > 0 ? `${m}:${pad(s)} ago` : `${s}s ago`;
    return `(last updated ${label})`;
}

function updateLastUpdated() {
    const el = document.getElementById('last-updated-ago');
    if (el) el.textContent = formatAgo(state.lastUpdatedAt);
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
}

function openZoom(dep, arrow) {
    unlockAudio();  // must happen inside tap handler so iOS allows audio
    zoom.active = true;
    // Add 59s so Math.floor always equals dep.minutes on open.
    // dep.minutes is a floor value (e.g. "9" means 9m0s – 9m59s remaining),
    // so without this buffer the modal would show one less minute immediately.
    zoom.departureTime = Date.now() + dep.minutes * 60_000 + 59_000;
    zoom.line = dep.line;
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

    const overlay = document.getElementById('modal-overlay');
    if (overlay) {
        overlay.classList.remove('closing');
        overlay.hidden = false;
    }
}

function closeZoom() {
    const overlay = document.getElementById('modal-overlay');
    if (!overlay || overlay.hidden) return;

    stopAlarm();
    zoom.active = false;
    zoom.alarmArmed = false;
    zoom.autoCloseArmed = false;
    document.getElementById('modal-card')?.classList.remove('alarming');

    overlay.classList.add('closing');
    setTimeout(() => {
        overlay.hidden = true;
        overlay.classList.remove('closing');
    }, 200);
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

    // Station title
    const titleEl = document.getElementById('station-title');
    if (titleEl) titleEl.textContent = data.station_name;

    // Stale badge — 200 with used_fallback means VBB down but snapshot served
    const staleBadge = document.getElementById('stale-badge');
    if (staleBadge) {
        if (data.used_fallback) {
            staleBadge.classList.add('visible');
            staleBadge.textContent = '⚠\u2009stale snapshot · VBB unreachable';
        } else {
            staleBadge.classList.remove('visible');
            staleBadge.textContent = '⚠\u2009stale data';
        }
    }

    // Rebuild quadrant cards — pass index for the cascade stagger
    grid.innerHTML = '';
    (data.quadrants || []).forEach((q, i) => grid.appendChild(createQuadrant(q, i)));
}

/**
 * Render a full-grid error state.
 * If we already have good data, leave the quadrants intact and only
 * update the stale badge — the last known departures are better than nothing.
 * @param {{ title: string, subtitle: string, badge: string }} copy
 * @param {boolean} isHard - true when no prior data exists (show full error card)
 */
function showError(copy, isHard = false) {
    console.error(`🚆 ${copy.title} — ${copy.subtitle}`);

    // Soft error: keep last good data on screen, just update the stale badge
    if (state.lastData && !isHard) {
        const staleBadge = document.getElementById('stale-badge');
        if (staleBadge) {
            staleBadge.classList.add('visible');
            staleBadge.textContent = copy.badge;
        }
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
}

// =============================================================================
// Data loop
// =============================================================================

async function refresh() {
    try {
        const data = await fetchDisplayData();
        state.lastData = data;
        state.lastUpdatedAt = Date.now();
        renderQuadrants(data);
        updateLastUpdated();
        console.info(`🚆 updated — station: ${data.station_name}, stale: ${data.used_fallback}`);
    } catch (err) {
        const copy = describeDisplayFetchError(err);
        // Hard error only when we've never had good data; otherwise keep last display intact
        showError(copy, /* isHard */ !state.lastData);
    }
}

// =============================================================================
// Boot
// =============================================================================

window.addEventListener('DOMContentLoaded', () => {
    updateClock();
    updateLastUpdated();
    setInterval(() => {
        updateClock();
        updateLastUpdated();
        updateZoomDisplay();
    }, DISPLAY_CONFIG.CLOCK_INTERVAL_MS);

    // Restore mute state on load
    const muteBtn = document.getElementById('mute-btn');
    if (muteBtn) {
        muteBtn.classList.toggle('muted', muted);
        muteBtn.setAttribute('aria-pressed', String(muted));
        muteBtn.setAttribute('aria-label', muted ? 'Unmute alarm' : 'Mute alarm');
        muteBtn.addEventListener('click', toggleMute);
    }

    // Close modal on backdrop click
    document.getElementById('modal-overlay')?.addEventListener('click', e => {
        if (e.target === e.currentTarget) closeZoom();
    });

    // Close modal on Escape
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeZoom();
    });

    // Initial load then recurring poll
    refresh();
    setInterval(refresh, DISPLAY_CONFIG.REFRESH_INTERVAL_MS);
});
