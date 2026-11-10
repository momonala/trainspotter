'use strict';

// =============================================================================
// Departure view model
// =============================================================================

/** Epoch ms of a departure's `when` ISO timestamp. */
function departureMs(dep) {
    return Date.parse(dep.when);
}

/** Whole minutes until a departure (floored, like the badge shows). */
function departureMinutes(dep, nowMs = Date.now()) {
    return Math.floor((departureMs(dep) - nowMs) / 60_000);
}

/**
 * Quadrants with computed `minutes`, dropping trains that are no longer
 * catchable (same min_departure_min gate as the server applies at fetch time).
 */
function currentQuadrants(data, nowMs = Date.now()) {
    const minMinutes = data?.min_departure_min ?? 5;
    return (data?.quadrants ?? []).map(q => ({
        ...q,
        departures: (q.departures ?? [])
            .map(dep => ({ ...dep, minutes: departureMinutes(dep, nowMs) }))
            .filter(dep => dep.minutes >= minMinutes),
    }));
}

/** JSON snapshot of quadrant departures — used to skip redundant re-renders. */
function departuresSnapshot(quadrants) {
    return JSON.stringify(
        quadrants.map(q => (q.departures ?? []).map(dep => `${dep.tripId}:${dep.minutes}`)),
    );
}

// =============================================================================
// Display fetch status
// =============================================================================

/**
 * @typedef {{ title: string, subtitle: string, badge: string, at: number, err: Error, consecutiveFailures: number }} FetchError
 * @typedef {{ status: 'loading' | 'fresh' | 'error', data: object | null, error: FetchError | null, updatedAt: number | null }} FetchStatus
 */

/** @type {FetchStatus} */
const INITIAL_FETCH_STATUS = Object.freeze({ status: 'loading', data: null, error: null, updatedAt: null });

/** @returns {FetchStatus} */
function fetchSucceeded(data, nowMs) {
    return { status: 'fresh', data, error: null, updatedAt: nowMs };
}

/** @returns {FetchStatus} */
function fetchFailed(previous, err, copy, nowMs) {
    return {
        status: 'error',
        data: null,
        error: {
            ...copy,
            at: nowMs,
            err,
            consecutiveFailures: (previous.error?.consecutiveFailures ?? 0) + 1,
        },
        updatedAt: previous.updatedAt,
    };
}

/**
 * Owns the single FetchStatus value. `refresh()` is a no-op (returns false)
 * while a previous refresh is still running.
 */
function createDisplayFetch({ load, describeError, now = Date.now }) {
    let current = INITIAL_FETCH_STATUS;
    let inFlight = false;

    return {
        get current() {
            return current;
        },
        async refresh({ onFresh, onError }) {
            if (inFlight) return false;
            inFlight = true;
            try {
                const data = await load();
                current = fetchSucceeded(data, now());
                onFresh(current);
            } catch (err) {
                current = fetchFailed(current, err, describeError(err), now());
                onError(current);
            } finally {
                inFlight = false;
            }
            return true;
        },
    };
}

/**
 * The next render for `fetchStatus`, or null when nothing changed since `rendered`.
 * A new FetchStatus value always renders; the same one renders only when a
 * departure's displayed minutes moved.
 */
function planRender(fetchStatus, rendered, nowMs = Date.now()) {
    if (!fetchStatus.data) return null;
    const quadrants = currentQuadrants(fetchStatus.data, nowMs);
    const snapshot = departuresSnapshot(quadrants);
    if (rendered?.source === fetchStatus && rendered.snapshot === snapshot) return null;
    return {
        source: fetchStatus,
        snapshot,
        quadrants,
        quadrantsByKey: new Map(quadrants.map(q => [q.key, q])),
    };
}

/** Quadrants of the last render, if that render belongs to the current FetchStatus. */
function renderedQuadrants(fetchStatus, rendered) {
    return rendered?.source === fetchStatus ? rendered.quadrantsByKey : new Map();
}

// =============================================================================
// Zoom modal and alarm
// =============================================================================

const ZOOM_TIMING = Object.freeze({
    ALARM_AT_MIN: 7,
    AUTO_CLOSE_AT_MIN: 5,
    BEEP_INTERVAL_MS: 1800,
    ALARM_AUTO_STOP_MS: 60_000,
});

/**
 * closed       modal hidden
 * alarm-armed  open, alarm fires at ALARM_AT_MIN, then auto-close at AUTO_CLOSE_AT_MIN
 * ringing      open, beeping; `autoClose` says whether auto-close is still pending
 * open-armed   open, silent, auto-close pending
 * dismissed    open, silent, waits for a manual close
 *
 * @typedef {'closed' | 'alarm-armed' | 'ringing' | 'open-armed' | 'dismissed'} ZoomPhase
 * @typedef {{ phase: ZoomPhase, tripId: string | null, departureTime: number | null, autoClose?: boolean, muted: boolean }} ZoomState
 */

/** Whole minutes left on the zoom countdown, never below zero. */
function zoomMinutesLeft(departureTime, nowMs) {
    return Math.max(0, Math.floor((departureTime - nowMs) / 60_000));
}

function isZoomOpen(zoomState) {
    return zoomState.phase !== 'closed';
}

function isZoomAlarming(zoomState) {
    return isZoomOpen(zoomState) && zoomState.phase !== 'alarm-armed';
}

function isAutoCloseArmed(zoomState) {
    switch (zoomState.phase) {
        case 'alarm-armed':
        case 'open-armed':
            return true;
        case 'ringing':
            return zoomState.autoClose;
        default:
            return false;
    }
}

function silencedPhase(autoClose) {
    return autoClose ? 'open-armed' : 'dismissed';
}

/**
 * Owns the zoom lifecycle and every alarm timer. All changes go through
 * `transition(event)`:
 *   { type: 'open', tripId, departureTime, minutes }
 *   { type: 'tick', now }
 *   { type: 'sync', departureTimeOf }   departureTimeOf(tripId) → ms, or null when the trip is gone
 *   { type: 'close' }
 *   { type: 'toggle-mute' }
 *   { type: 'auto-stop' }
 */
function createZoomMachine({ timers, beep, requestClose, onMuteChange, onChange, muted = false }) {
    /** @type {ZoomState} */
    let state = { phase: 'closed', tripId: null, departureTime: null, muted };
    let sound = null;

    function beepUnlessMuted() {
        if (!state.muted) beep();
    }

    function stopSound() {
        if (!sound) return;
        timers.clearInterval(sound.intervalId);
        timers.clearTimeout(sound.stopId);
        sound = null;
    }

    function startSound() {
        stopSound();
        beepUnlessMuted();
        sound = {
            intervalId: timers.setInterval(beepUnlessMuted, ZOOM_TIMING.BEEP_INTERVAL_MS),
            stopId: timers.setTimeout(() => transition({ type: 'auto-stop' }), ZOOM_TIMING.ALARM_AUTO_STOP_MS),
        };
    }

    function silence() {
        stopSound();
        if (state.phase === 'ringing') {
            const { autoClose, ...rest } = state;
            state = { ...rest, phase: silencedPhase(autoClose) };
        }
    }

    function apply(event) {
        switch (event.type) {
            case 'open': {
                const opened = { tripId: event.tripId, departureTime: event.departureTime, muted: state.muted };
                if (event.minutes > ZOOM_TIMING.ALARM_AT_MIN) {
                    state = { ...opened, phase: 'alarm-armed' };
                } else {
                    state = { ...opened, phase: 'ringing', autoClose: event.minutes > ZOOM_TIMING.AUTO_CLOSE_AT_MIN };
                    startSound();
                }
                return;
            }
            case 'tick': {
                if (!isZoomOpen(state)) return;
                const minutes = zoomMinutesLeft(state.departureTime, event.now);
                if (state.phase === 'alarm-armed' && minutes <= ZOOM_TIMING.ALARM_AT_MIN) {
                    state = { ...state, phase: 'ringing', autoClose: true };
                    startSound();
                }
                if (isAutoCloseArmed(state) && minutes <= ZOOM_TIMING.AUTO_CLOSE_AT_MIN) {
                    state = state.phase === 'ringing'
                        ? { ...state, autoClose: false }
                        : { ...state, phase: 'dismissed' };
                    requestClose();
                }
                return;
            }
            case 'sync': {
                if (!isZoomOpen(state) || !state.tripId) return;
                const departureTime = event.departureTimeOf(state.tripId);
                if (departureTime == null) return;
                state = { ...state, departureTime };
                return;
            }
            case 'close':
                stopSound();
                state = { phase: 'closed', tripId: null, departureTime: null, muted: state.muted };
                return;
            case 'toggle-mute':
                state = { ...state, muted: !state.muted };
                onMuteChange(state.muted);
                if (state.muted) silence();
                return;
            case 'auto-stop':
                silence();
                return;
            default:
                throw new Error(`unknown zoom event: ${event.type}`);
        }
    }

    function transition(event) {
        apply(event);
        onChange(state);
    }

    return {
        get state() {
            return state;
        },
        transition,
    };
}

if (typeof module !== 'undefined') {
    module.exports = {
        INITIAL_FETCH_STATUS,
        ZOOM_TIMING,
        createDisplayFetch,
        createZoomMachine,
        isZoomAlarming,
        isZoomOpen,
        zoomMinutesLeft,
        currentQuadrants,
        departureMinutes,
        departureMs,
        departuresSnapshot,
        fetchFailed,
        fetchSucceeded,
        planRender,
        renderedQuadrants,
    };
}
