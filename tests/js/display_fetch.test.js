'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
    INITIAL_FETCH_STATUS,
    createDisplayFetch,
    planRender,
    renderedQuadrants,
    retryNotice,
} = require('../../static/display-state.js');

const NOW = Date.parse('2026-11-10T08:00:00Z');

function at(minutesFromNow, base = NOW) {
    return new Date(base + minutesFromNow * 60_000).toISOString();
}

function fakeData({ minDepartureMin = 5 } = {}) {
    return {
        station_name: 'Warschauer Str',
        min_departure_min: minDepartureMin,
        quadrants: [
            {
                key: 'north',
                label: 'S1',
                arrow: '↑',
                departures: [
                    { tripId: 'a', line: 'S1', when: at(4) },
                    { tripId: 'b', line: 'S1', when: at(9) },
                ],
            },
            { key: 'south', label: 'U1', arrow: '↓', departures: [{ tripId: 'c', line: 'U1', when: at(12) }] },
        ],
    };
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

function fakeFetch({ now = () => NOW } = {}) {
    const pending = [];
    const calls = { fresh: [], error: [] };
    const store = createDisplayFetch({
        load: () => {
            const d = deferred();
            pending.push(d);
            return d.promise;
        },
        describeError: err => ({ title: `T:${err.message}`, subtitle: 'S', badge: 'B' }),
        now,
    });
    const handlers = {
        onFresh: value => calls.fresh.push(value),
        onError: value => calls.error.push(value),
    };
    return { store, pending, calls, refresh: () => store.refresh(handlers) };
}

test('loading -> fresh sets data and updatedAt in one value', async () => {
    const { store, pending, calls, refresh } = fakeFetch({ now: () => NOW + 1234 });
    const data = fakeData();
    const run = refresh();
    assert.equal(store.current.status, 'loading');
    pending[0].resolve(data);
    assert.equal(await run, true);
    assert.deepEqual(store.current, { status: 'fresh', data, error: null, updatedAt: NOW + 1234 });
    assert.deepEqual(calls.fresh, [store.current]);
    assert.deepEqual(calls.error, []);
});

test('loading -> error with no old data has no updatedAt', async () => {
    const { store, pending, calls, refresh } = fakeFetch();
    const run = refresh();
    const err = new Error('boom');
    pending[0].reject(err);
    assert.equal(await run, true);
    assert.equal(store.current.status, 'error');
    assert.equal(store.current.data, null);
    assert.equal(store.current.updatedAt, null);
    assert.deepEqual(store.current.error, {
        title: 'T:boom', subtitle: 'S', badge: 'B', at: NOW, err, consecutiveFailures: 1,
    });
    assert.deepEqual(calls.error, [store.current]);
});

test('fresh -> error drops data but keeps the last success time', async () => {
    let now = NOW;
    const { store, pending, refresh } = fakeFetch({ now: () => now });
    let run = refresh();
    pending[0].resolve(fakeData());
    await run;

    now = NOW + 10_000;
    run = refresh();
    pending[1].reject(new Error('down'));
    await run;
    assert.equal(store.current.status, 'error');
    assert.equal(store.current.data, null);
    assert.equal(store.current.updatedAt, NOW);
    assert.equal(store.current.error.at, NOW + 10_000);
});

test('consecutive failures count up and reset after a success', async () => {
    const { store, pending, refresh } = fakeFetch();
    for (let i = 0; i < 3; i++) {
        const run = refresh();
        pending[i].reject(new Error(`e${i}`));
        await run;
        assert.equal(store.current.error.consecutiveFailures, i + 1);
    }
    let run = refresh();
    pending[3].resolve(fakeData());
    await run;
    assert.equal(store.current.status, 'fresh');
    assert.equal(store.current.error, null);

    run = refresh();
    pending[4].reject(new Error('again'));
    await run;
    assert.equal(store.current.error.consecutiveFailures, 1);
});

test('refresh is skipped while one is in flight, and allowed again after', async () => {
    const { pending, refresh } = fakeFetch();
    const first = refresh();
    const second = refresh();
    assert.equal(pending.length, 1);
    assert.equal(await second, false);
    pending[0].resolve(fakeData());
    assert.equal(await first, true);

    const next = refresh();
    assert.equal(pending.length, 2);
    pending[1].reject(new Error('x'));
    assert.equal(await next, true);
    const after = refresh();
    assert.equal(pending.length, 3);
    pending[2].resolve(fakeData());
    await after;
});

test('a throwing fresh handler is recorded as an error, keeping updatedAt', async () => {
    const store = createDisplayFetch({
        load: async () => fakeData(),
        describeError: err => ({ title: err.message, subtitle: '', badge: '' }),
        now: () => NOW,
    });
    const errors = [];
    await store.refresh({
        onFresh: () => { throw new Error('render failed'); },
        onError: value => errors.push(value),
    });
    assert.equal(store.current.status, 'error');
    assert.equal(store.current.data, null);
    assert.equal(store.current.updatedAt, NOW);
    assert.equal(store.current.error.title, 'render failed');
    assert.equal(store.current.error.consecutiveFailures, 1);
    assert.equal(errors.length, 1);
});

test('a throwing error handler propagates and the next refresh still runs', async () => {
    let calls = 0;
    const store = createDisplayFetch({
        load: async () => { calls++; throw new Error('x'); },
        describeError: () => ({ title: '', subtitle: '', badge: '' }),
        now: () => NOW,
    });
    const handlers = { onFresh: () => {}, onError: () => { throw new Error('ui'); } };
    await assert.rejects(store.refresh(handlers), /ui/);
    await assert.rejects(store.refresh(handlers), /ui/);
    assert.equal(calls, 2);
});

test('planRender renders nothing without data', () => {
    assert.equal(planRender(INITIAL_FETCH_STATUS, null, NOW), null);
    const failed = { status: 'error', data: null, error: {}, updatedAt: NOW };
    assert.equal(planRender(failed, null, NOW), null);
});

test('planRender derives minutes, drops uncatchable trains and indexes by key', () => {
    const value = { status: 'fresh', data: fakeData(), error: null, updatedAt: NOW };
    const plan = planRender(value, null, NOW);
    assert.equal(plan.source, value);
    assert.deepEqual(plan.quadrants[0].departures.map(d => [d.tripId, d.minutes]), [['b', 9]]);
    assert.deepEqual(plan.quadrants[1].departures.map(d => [d.tripId, d.minutes]), [['c', 12]]);
    assert.equal(plan.snapshot, JSON.stringify([['b:9'], ['c:12']]));
    assert.deepEqual([...plan.quadrantsByKey.keys()], ['north', 'south']);
    assert.equal(plan.quadrantsByKey.get('north'), plan.quadrants[0]);
});

test('planRender keeps a departure exactly at min_departure_min', () => {
    const data = fakeData({ minDepartureMin: 4 });
    const plan = planRender({ status: 'fresh', data, error: null, updatedAt: NOW }, null, NOW);
    assert.deepEqual(plan.quadrants[0].departures.map(d => d.tripId), ['a', 'b']);
});

test('planRender skips when the same value shows the same minutes', () => {
    const value = { status: 'fresh', data: fakeData(), error: null, updatedAt: NOW };
    const first = planRender(value, null, NOW);
    assert.equal(planRender(value, first, NOW - 59_999), null);
});

test('planRender re-renders the same value when a displayed minute changes', () => {
    const value = { status: 'fresh', data: fakeData(), error: null, updatedAt: NOW };
    const first = planRender(value, null, NOW);
    const next = planRender(value, first, NOW + 1);
    assert.notEqual(next, null);
    assert.equal(next.snapshot, JSON.stringify([['b:8'], ['c:11']]));
});

test('planRender always renders a new value, even with identical departures', () => {
    const data = fakeData();
    const first = planRender({ status: 'fresh', data, error: null, updatedAt: NOW }, null, NOW);
    const again = { status: 'fresh', data, error: null, updatedAt: NOW + 10_000 };
    const plan = planRender(again, first, NOW);
    assert.notEqual(plan, null);
    assert.equal(plan.snapshot, first.snapshot);
    assert.equal(plan.source, again);
});

test('renderedQuadrants is the last render index only while its value is current', () => {
    const value = { status: 'fresh', data: fakeData(), error: null, updatedAt: NOW };
    const plan = planRender(value, null, NOW);
    assert.equal(renderedQuadrants(value, plan), plan.quadrantsByKey);

    const failed = { status: 'error', data: null, error: {}, updatedAt: NOW };
    assert.equal(renderedQuadrants(failed, plan).size, 0);
    assert.equal(renderedQuadrants(INITIAL_FETCH_STATUS, null).size, 0);
});

test('retryNotice renders the refresh interval in seconds', () => {
    assert.equal(retryNotice(10_000), 'Retrying every 10s');
});
