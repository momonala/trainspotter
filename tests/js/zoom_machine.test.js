'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
    createZoomMachine,
    isZoomAlarming,
    isZoomOpen,
    zoomMinutesLeft,
} = require('../../static/display-state.js');

const T0 = Date.parse('2026-11-10T08:00:00Z');
const MIN = 60_000;

function fakeClock(start = T0) {
    let now = start;
    let nextId = 1;
    const tasks = new Map();

    function schedule(fn, ms, repeat) {
        const id = nextId++;
        tasks.set(id, { fn, at: now + ms, ms, repeat });
        return id;
    }

    return {
        now: () => now,
        pending: () => tasks.size,
        setTimeout: (fn, ms) => schedule(fn, ms, false),
        setInterval: (fn, ms) => schedule(fn, ms, true),
        clearTimeout: id => tasks.delete(id),
        clearInterval: id => tasks.delete(id),
        advance(ms) {
            const end = now + ms;
            for (;;) {
                let nextIdKey = null;
                let next = null;
                for (const [id, task] of tasks) {
                    if (task.at <= end && (!next || task.at < next.at)) {
                        nextIdKey = id;
                        next = task;
                    }
                }
                if (!next) break;
                now = next.at;
                if (next.repeat) next.at += next.ms;
                else tasks.delete(nextIdKey);
                next.fn();
            }
            now = end;
        },
    };
}

function setup({ muted = false, closeOnRequest = true } = {}) {
    const clock = fakeClock();
    const log = { beeps: [], closeRequests: 0, muteWrites: [], changes: [] };
    let machine;
    machine = createZoomMachine({
        timers: clock,
        beep: () => log.beeps.push(clock.now()),
        requestClose: () => {
            log.closeRequests++;
            if (closeOnRequest) machine.transition({ type: 'close' });
        },
        onMuteChange: value => log.muteWrites.push(value),
        onChange: state => log.changes.push(state),
        muted,
    });
    const open = (minutes, { tripId = 't1', departureTime = clock.now() + minutes * MIN } = {}) =>
        machine.transition({ type: 'open', tripId, departureTime, minutes });
    const tick = () => machine.transition({ type: 'tick', now: clock.now() });
    const phase = () => machine.state.phase;
    return { clock, log, machine, open, tick, phase };
}

test('zoomMinutesLeft floors and never goes below zero', () => {
    assert.equal(zoomMinutesLeft(T0 + 7 * MIN, T0), 7);
    assert.equal(zoomMinutesLeft(T0 + 7 * MIN, T0 + 1), 6);
    assert.equal(zoomMinutesLeft(T0, T0 + 5 * MIN), 0);
});

test('starts closed with no timers, muted only when the stored preference says so', () => {
    const { machine, clock } = setup();
    assert.deepEqual(machine.state, { phase: 'closed', tripId: null, departureTime: null, muted: false });
    assert.equal(isZoomOpen(machine.state), false);
    assert.equal(clock.pending(), 0);
    assert.equal(setup({ muted: true }).machine.state.muted, true);
});

test('open above 7 min -> alarm-armed, silent, not alarming', () => {
    const { machine, open, log, clock } = setup();
    open(8);
    assert.equal(machine.state.phase, 'alarm-armed');
    assert.equal(machine.state.tripId, 't1');
    assert.equal(machine.state.departureTime, T0 + 8 * MIN);
    assert.equal(isZoomAlarming(machine.state), false);
    assert.equal(log.beeps.length, 0);
    assert.equal(clock.pending(), 0);
});

test('open at exactly 7 min -> ringing at once, auto-close still armed', () => {
    const { machine, open, log } = setup();
    open(7);
    assert.equal(machine.state.phase, 'ringing');
    assert.equal(machine.state.autoClose, true);
    assert.equal(isZoomAlarming(machine.state), true);
    assert.deepEqual(log.beeps, [T0]);
});

test('open at exactly 5 min -> ringing with no auto-close', () => {
    const { machine, open } = setup();
    open(5);
    assert.equal(machine.state.phase, 'ringing');
    assert.equal(machine.state.autoClose, false);
});

test('alarm-armed -> ringing when the countdown reaches 7 min', () => {
    const { clock, open, tick, phase, log } = setup();
    open(8);
    tick();
    assert.equal(phase(), 'alarm-armed');
    assert.equal(log.beeps.length, 0);
    clock.advance(1);
    tick();
    assert.equal(phase(), 'ringing');
    assert.equal(log.closeRequests, 0);
    assert.deepEqual(log.beeps, [T0 + 1]);
});

test('ringing beeps every 1800 ms', () => {
    const { clock, open, log } = setup();
    open(7);
    clock.advance(1799);
    assert.equal(log.beeps.length, 1);
    clock.advance(1);
    assert.deepEqual(log.beeps, [T0, T0 + 1800]);
    clock.advance(1800 * 3);
    assert.equal(log.beeps.length, 5);
});

test('ringing auto-stops after exactly 60 s and stays open', () => {
    const { clock, open, phase, machine, log } = setup();
    open(6, { departureTime: T0 + 30 * MIN });
    clock.advance(59_999);
    assert.equal(phase(), 'ringing');
    clock.advance(1);
    assert.equal(phase(), 'open-armed');
    assert.equal(isZoomAlarming(machine.state), true);
    assert.equal(clock.pending(), 0);
    const beeps = log.beeps.length;
    assert.equal(beeps, 1 + Math.floor(60_000 / 1800));
    clock.advance(10 * MIN);
    assert.equal(log.beeps.length, beeps);
});

test('auto-stop without pending auto-close -> dismissed', () => {
    const { clock, open, phase, machine } = setup();
    open(3);
    clock.advance(60_000);
    assert.equal(phase(), 'dismissed');
    assert.equal(isZoomOpen(machine.state), true);
    assert.equal(isZoomAlarming(machine.state), true);
});

test('ringing -> auto-close request at 5 min, which closes and stops the sound', () => {
    const { clock, open, tick, phase, log } = setup();
    open(6, { departureTime: T0 + 6.5 * MIN });
    clock.advance(30_000);
    tick();
    assert.equal(log.closeRequests, 0);
    assert.equal(phase(), 'ringing');
    clock.advance(1);
    tick();
    assert.equal(log.closeRequests, 1);
    assert.equal(phase(), 'closed');
    assert.equal(clock.pending(), 0);
});

test('open-armed -> auto-close request at 5 min', () => {
    const { clock, open, tick, phase, log } = setup();
    open(6, { departureTime: T0 + 6 * MIN + 30_000 });
    clock.advance(60_000);
    assert.equal(phase(), 'open-armed');
    clock.advance(30_000);
    tick();
    assert.equal(log.closeRequests, 1);
    assert.equal(phase(), 'closed');
});

test('alarm fires and auto-close is requested on the same tick when the countdown jumps past 5 min', () => {
    const { clock, open, tick, phase, log } = setup();
    open(10);
    clock.advance(5 * MIN);
    tick();
    assert.deepEqual(log.beeps, [T0 + 5 * MIN]);
    assert.equal(log.closeRequests, 1);
    assert.equal(phase(), 'closed');
    assert.equal(clock.pending(), 0);
});

test('an ignored close request disarms auto-close but keeps ringing', () => {
    const { clock, open, tick, phase, machine, log } = setup({ closeOnRequest: false });
    open(6, { departureTime: T0 + 5.5 * MIN });
    tick();
    assert.equal(phase(), 'ringing');
    assert.equal(machine.state.autoClose, false);
    tick();
    assert.equal(log.closeRequests, 1);
    clock.advance(60_000);
    assert.equal(phase(), 'dismissed');
});

test('an ignored close request from open-armed -> dismissed', () => {
    const { clock, open, tick, phase, log } = setup({ closeOnRequest: false });
    open(6, { departureTime: T0 + 6 * MIN + 30_000 });
    clock.advance(90_000);
    tick();
    assert.equal(phase(), 'dismissed');
    tick();
    assert.equal(log.closeRequests, 1);
});

test('close from any open phase resets and clears every timer', () => {
    for (const minutes of [8, 7, 4]) {
        const { machine, open, clock, log } = setup();
        open(minutes);
        machine.transition({ type: 'close' });
        assert.deepEqual(machine.state, { phase: 'closed', tripId: null, departureTime: null, muted: false });
        assert.equal(clock.pending(), 0);
        const beeps = log.beeps.length;
        clock.advance(5 * MIN);
        assert.equal(log.beeps.length, beeps);
    }
});

test('mute while ringing stops the sound and keeps the modal open', () => {
    const { machine, open, clock, log, phase } = setup();
    open(6);
    machine.transition({ type: 'toggle-mute' });
    assert.equal(machine.state.muted, true);
    assert.deepEqual(log.muteWrites, [true]);
    assert.equal(phase(), 'open-armed');
    assert.equal(clock.pending(), 0);
});

test('mute while ringing with no auto-close -> dismissed', () => {
    const { machine, open, phase } = setup();
    open(4);
    machine.transition({ type: 'toggle-mute' });
    assert.equal(phase(), 'dismissed');
});

test('unmute does not restart a stopped alarm', () => {
    const { machine, open, clock, log, phase } = setup();
    open(6);
    machine.transition({ type: 'toggle-mute' });
    machine.transition({ type: 'toggle-mute' });
    assert.deepEqual(log.muteWrites, [true, false]);
    assert.equal(machine.state.muted, false);
    assert.equal(phase(), 'open-armed');
    clock.advance(10_000);
    assert.equal(log.beeps.length, 1);
});

test('muted before the alarm: it still rings silently and unmuting makes it audible', () => {
    const { machine, open, tick, clock, log, phase } = setup({ muted: true });
    open(8);
    clock.advance(MIN);
    tick();
    assert.equal(phase(), 'ringing');
    assert.equal(log.beeps.length, 0);
    clock.advance(1800 * 2);
    assert.equal(log.beeps.length, 0);
    machine.transition({ type: 'toggle-mute' });
    assert.equal(phase(), 'ringing');
    clock.advance(1800);
    assert.equal(log.beeps.length, 1);
    clock.advance(60_000);
    assert.equal(phase(), 'open-armed');
});

test('mute is kept across close and reopen', () => {
    const { machine, open, log } = setup();
    machine.transition({ type: 'toggle-mute' });
    assert.equal(machine.state.phase, 'closed');
    machine.transition({ type: 'close' });
    open(7);
    assert.equal(machine.state.muted, true);
    assert.equal(log.beeps.length, 0);
});

test('sync moves the countdown to the live departure time', () => {
    const { machine, open, clock, tick, phase } = setup();
    open(10, { tripId: 'trip-9' });
    const asked = [];
    machine.transition({
        type: 'sync',
        departureTimeOf: id => { asked.push(id); return T0 + 6 * MIN; },
    });
    assert.deepEqual(asked, ['trip-9']);
    assert.equal(machine.state.departureTime, T0 + 6 * MIN);
    tick();
    assert.equal(phase(), 'ringing');
    clock.advance(MIN);
    tick();
    assert.equal(phase(), 'closed');
});

test('sync keeps the last known time when the trip is gone from the data', () => {
    const { machine, open } = setup();
    open(10);
    machine.transition({ type: 'sync', departureTimeOf: () => null });
    assert.equal(machine.state.departureTime, T0 + 10 * MIN);
    assert.equal(machine.state.phase, 'alarm-armed');
});

test('sync is ignored when closed', () => {
    const { machine } = setup();
    let asked = 0;
    machine.transition({ type: 'sync', departureTimeOf: () => { asked++; return T0; } });
    assert.equal(asked, 0);
    assert.equal(machine.state.departureTime, null);
});

test('reopening an urgent train restarts the alarm and its 60 s auto-stop', () => {
    const { open, clock, phase, log } = setup();
    open(6, { departureTime: T0 + 30 * MIN });
    clock.advance(30_000);
    open(6, { tripId: 't2', departureTime: T0 + 30 * MIN });
    assert.equal(log.beeps.at(-1), T0 + 30_000);
    assert.equal(clock.pending(), 2);
    clock.advance(59_999);
    assert.equal(phase(), 'ringing');
    clock.advance(1);
    assert.equal(phase(), 'open-armed');
});

test('reopening a far train while ringing keeps the old sound until its own auto-stop', () => {
    const { open, clock, phase, machine, log } = setup();
    open(6, { departureTime: T0 + 30 * MIN });
    clock.advance(10_000);
    open(9, { tripId: 't2', departureTime: T0 + 40 * MIN });
    assert.equal(phase(), 'alarm-armed');
    assert.equal(isZoomAlarming(machine.state), false);
    const beeps = log.beeps.length;
    clock.advance(1800);
    assert.equal(log.beeps.length, beeps + 1);
    clock.advance(50_000 - 1800);
    assert.equal(clock.pending(), 0);
    assert.equal(phase(), 'alarm-armed');
});

test('mute in alarm-armed stops a leftover sound without changing phase', () => {
    const { open, clock, phase, machine } = setup();
    open(6, { departureTime: T0 + 30 * MIN });
    open(9, { tripId: 't2', departureTime: T0 + 40 * MIN });
    machine.transition({ type: 'toggle-mute' });
    assert.equal(phase(), 'alarm-armed');
    assert.equal(clock.pending(), 0);
});

test('every transition reports the new state, and a close request during tick reports closed last', () => {
    const { machine, clock, open, tick, log } = setup();
    open(8);
    tick();
    machine.transition({ type: 'close' });
    assert.deepEqual(log.changes.map(s => s.phase), ['alarm-armed', 'alarm-armed', 'closed']);

    open(6);
    clock.advance(MIN);
    tick();
    assert.equal(log.changes.at(-1).phase, 'closed');
});
