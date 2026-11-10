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

if (typeof module !== 'undefined') {
    module.exports = {
        INITIAL_FETCH_STATUS,
        createDisplayFetch,
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
