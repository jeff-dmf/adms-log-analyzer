// Follow tail flow with a fake api and a fake grid view: no DOM, no real requests.
import test from "node:test";
import assert from "node:assert/strict";

import { createDebounce, createFollowFlow } from "../../src/dmslog/web/js/follow-tail-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup(info = { atEnd: true, top: 180, visible: 20, length: 200 }) {
  const counts = [];
  const states = [];
  const calls = { followTail: 0 };
  const current = { caseId: "A", info: { ...info }, total: 1000 };
  const api = {
    count(caseId, filter, signal) {
      const d = deferred();
      counts.push({ caseId, filter, signal, d });
      return d.promise;
    },
  };
  const filter = { and: [] }; // the same object until the filter changes, as in the app
  const flow = createFollowFlow({
    api,
    getCase: () => current.caseId,
    getFilter: () => filter,
    view: {
      tailInfo: () => current.info,
      followTail: async () => { calls.followTail += 1; },
      total: () => current.total,
    },
    onState: (s) => states.push(s),
  });
  return { flow, counts, states, calls, current };
}

test("nothing happens while it is off", async () => {
  const { flow, calls, counts } = setup();
  await flow.refresh();
  assert.equal(calls.followTail, 0);
  assert.equal(counts.length, 0);
});

test("at the bottom the tail is reloaded and the counter stays at zero", async () => {
  const { flow, calls, counts, states } = setup();
  flow.setOn(true);
  await flow.refresh();
  assert.equal(calls.followTail, 1);
  assert.equal(counts.length, 0);
  assert.equal(states.at(-1).pending, 0);
});

test("scrolled up the view is left alone and the new records are counted", async () => {
  const { flow, calls, counts, states, current } = setup({ atEnd: true, top: 100, visible: 20, length: 200 });
  flow.setOn(true);
  const refreshing = flow.refresh();
  assert.equal(counts.length, 1);
  assert.equal(counts[0].caseId, "A");
  counts[0].d.resolve({ count: 1042, exact: true });
  await refreshing;
  assert.equal(calls.followTail, 0);
  assert.equal(flow.state.pending, 42);
  assert.equal(states.at(-1).pending, 42);
  // Back at the end of the OLD buffer: the tail is loaded first, only then does the counter clear.
  current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  flow.viewChanged();
  assert.equal(calls.followTail, 1);
  assert.equal(flow.state.pending, 42);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(flow.state.pending, 0);
});

test("returning to the bottom with nothing pending does not reload", () => {
  const { flow, calls, current } = setup({ atEnd: true, top: 100, visible: 20, length: 200 });
  flow.setOn(true);
  current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  flow.viewChanged();
  assert.equal(calls.followTail, 0);
});

test("a stale count answer is dropped after a newer refresh", async () => {
  const { flow, counts } = setup({ atEnd: false, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  const first = flow.refresh();
  const second = flow.refresh();
  assert.equal(counts[0].signal.aborted, true);
  counts[0].d.resolve({ count: 5000 });
  await first;
  assert.equal(flow.state.pending, 0);
  counts[1].d.resolve({ count: 1010 });
  await second;
  assert.equal(flow.state.pending, 10);
});

test("an answer for another case or after switching off is dropped", async () => {
  const { flow, counts, current } = setup({ atEnd: true, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  const first = flow.refresh();
  current.caseId = "B";
  counts[0].d.resolve({ count: 1500 });
  await first;
  assert.equal(flow.state.pending, 0);
  current.caseId = "A";
  const second = flow.refresh();
  flow.setOn(false);
  counts[1].d.resolve({ count: 1500 });
  await second;
  assert.equal(flow.state.pending, 0);
});

test("a failed count keeps the previous number", async () => {
  const { flow, counts } = setup({ atEnd: false, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  const refreshing = flow.refresh();
  counts[0].d.reject(new Error("network"));
  await refreshing;
  assert.equal(flow.state.pending, 0);
});

test("changing case resets the counter", () => {
  const { flow, states } = setup({ atEnd: false, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  flow.state.pending = 7;
  flow.reset();
  assert.equal(states.at(-1).pending, 0);
});

function fakeTimers() {
  let now = 0;
  let nextId = 1;
  const queue = new Map();
  return {
    now: () => now,
    setTimeout(fn, ms) {
      const id = nextId++;
      queue.set(id, { at: now + ms, fn });
      return id;
    },
    clearTimeout(id) { queue.delete(id); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...queue.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        queue.delete(due[0]);
        now = due[1].at;
        due[1].fn();
      }
      now = end;
    },
  };
}

test("a debounce fires after the quiet period", () => {
  const timers = fakeTimers();
  let runs = 0;
  const d = createDebounce({ run: () => { runs += 1; }, wait: 400, maxWait: 2000, timers });
  d.trigger();
  timers.advance(399);
  assert.equal(runs, 0);
  timers.advance(1);
  assert.equal(runs, 1);
  assert.equal(d.pending(), false);
});

test("continuous events every 200 ms still fire within the maximum wait", () => {
  const timers = fakeTimers();
  const firedAt = [];
  const d = createDebounce({ run: () => firedAt.push(timers.now()), wait: 400, maxWait: 2000, timers });
  for (let t = 0; t < 7000; t += 200) {
    d.trigger();
    timers.advance(200);
  }
  assert.ok(firedAt.length >= 3, String(firedAt));
  assert.ok(firedAt[0] <= 2000);
  assert.ok(firedAt.every((at, i) => i === 0 || at - firedAt[i - 1] <= 2200));
});

test("the slower derived debounce is bounded by its own maximum wait", () => {
  const timers = fakeTimers();
  let runs = 0;
  const d = createDebounce({ run: () => { runs += 1; }, wait: 1500, maxWait: 6000, timers });
  for (let t = 0; t < 6000; t += 1000) {
    d.trigger();
    timers.advance(1000);
  }
  assert.equal(runs, 1);
});

test("cancel drops a pending run", () => {
  const timers = fakeTimers();
  let runs = 0;
  const d = createDebounce({ run: () => { runs += 1; }, wait: 100, maxWait: 500, timers });
  d.trigger();
  d.cancel();
  timers.advance(1000);
  assert.equal(runs, 0);
});

function setupTotals(info) {
  const ctx = setup(info);
  const shown = [];
  const view = {
    tailInfo: () => ctx.current.info,
    followTail: async () => { ctx.calls.followTail += 1; },
    total: () => ctx.current.total,
    showTotal: (n, exact) => { shown.push({ n, exact }); ctx.current.total = n; return true; },
  };
  const flow = createFollowFlow({
    api: { count: (caseId, filter, signal) => { const d = deferred(); ctx.counts.push({ caseId, filter, signal, d }); return d.promise; } },
    getCase: () => ctx.current.caseId,
    getFilter: () => ctx.current.filter,
    view,
    onState: (s) => ctx.states.push(s),
  });
  ctx.current.filter = { and: [] };
  return { ...ctx, flow, shown };
}

test("one total: every count answer is shown by the grid and the baseline stays at the first view total", async () => {
  const { flow, counts, shown, states, current } = setupTotals({ atEnd: true, top: 100, visible: 20, length: 200 });
  current.total = 32187;
  flow.setOn(true);
  for (const total of [32500, 38000, 45637]) {
    const refreshing = flow.refresh();
    counts.at(-1).d.resolve({ count: total, exact: true });
    await refreshing;
  }
  assert.deepEqual(shown.map((s) => s.n), [32500, 38000, 45637]);
  assert.equal(flow.state.total, 45637);
  assert.equal(current.total, 45637); // the grid (status) shows the same total
  assert.equal(flow.state.pending, 45637 - 32187); // "new records" = total - the total when the view left the end
  assert.equal(states.at(-1).pending, 13450);
});

test("one total: reaching the bottom again forgets the baseline", async () => {
  const { flow, counts, current, calls } = setupTotals({ atEnd: true, top: 100, visible: 20, length: 200 });
  current.total = 1000;
  flow.setOn(true);
  const refreshing = flow.refresh();
  counts[0].d.resolve({ count: 1100 });
  await refreshing;
  current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  flow.viewChanged();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(calls.followTail, 1);
  assert.equal(flow.state.pending, 0);
  current.total = 1100;
  current.info = { atEnd: true, top: 100, visible: 20, length: 200 };
  flow.viewChanged();
  const next = flow.refresh();
  counts[1].d.resolve({ count: 1105 });
  await next;
  assert.equal(flow.state.pending, 5); // measured from 1100, not from 1000
});

test("one total: a grid that reloaded with its own total moves the baseline", async () => {
  const { flow, counts, current } = setupTotals({ atEnd: true, top: 100, visible: 20, length: 200 });
  current.total = 1000;
  flow.setOn(true);
  const first = flow.refresh();
  counts[0].d.resolve({ count: 1050 });
  await first;
  current.total = 400; // the user changed the view: the grid counted its own total
  const second = flow.refresh();
  counts[1].d.resolve({ count: 420 });
  await second;
  assert.equal(flow.state.pending, 20);
});

test("one total: an answer for a filter that changed meanwhile is dropped", async () => {
  const { flow, counts, shown, current } = setupTotals({ atEnd: true, top: 100, visible: 20, length: 200 });
  current.total = 10;
  flow.setOn(true);
  const refreshing = flow.refresh();
  current.filter = { and: [{ x: 1 }] };
  counts[0].d.resolve({ count: 99 });
  await refreshing;
  assert.equal(shown.length, 0);
  assert.equal(flow.state.pending, 0);
});

test("one total: an estimate stays marked as such", async () => {
  const { flow, counts, shown, current } = setupTotals({ atEnd: true, top: 100, visible: 20, length: 200 });
  current.total = 10;
  flow.setOn(true);
  const refreshing = flow.refresh();
  counts[0].d.resolve({ count: 99, exact: false });
  await refreshing;
  assert.equal(shown[0].exact, false);
});

test("engage goes to the end from anywhere; scrolling up afterwards stops following", async () => {
  const { flow, calls, counts, current, states } = setupTotals({ atEnd: false, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  await flow.engage();
  assert.equal(calls.followTail, 1);
  current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  assert.equal(states.at(-1).pending, 0);
  flow.viewChanged();
  current.info = { atEnd: true, top: 100, visible: 20, length: 200 }; // the user scrolls up
  flow.viewChanged();
  const refreshing = flow.refresh(); // a watch event: counted, not followed
  assert.equal(calls.followTail, 1);
  counts[0].d.resolve({ count: 1001 });
  await refreshing;
  assert.equal(calls.followTail, 1);
});

test("engage does nothing when off or without a case, and a newer one wins", async () => {
  const { flow, calls, current } = setupTotals();
  await flow.engage();
  assert.equal(calls.followTail, 0);
  flow.setOn(true);
  current.caseId = null;
  await flow.engage();
  assert.equal(calls.followTail, 0);
  current.caseId = "A";
  const p = flow.engage();
  assert.equal(flow.busy(), true);
  flow.setOn(false);
  assert.equal(flow.busy(), false);
  await p;
});

test("an engage superseded by a reset leaves the state alone when its reload ends", async () => {
  const ctx = setupTotals({ atEnd: false, top: 0, visible: 20, length: 200 });
  const gate = deferred();
  const flow = createFollowFlow({
    api: {}, getCase: () => ctx.current.caseId, getFilter: () => null,
    view: { tailInfo: () => ctx.current.info, followTail: () => gate.promise, total: () => 1 },
    onState: (s) => ctx.states.push(s),
  });
  flow.setOn(true);
  const before = ctx.states.length;
  const p = flow.engage();
  flow.reset();
  const afterReset = ctx.states.length;
  gate.resolve();
  await p;
  assert.equal(ctx.states.length, afterReset);
  assert.ok(afterReset > before);
});
